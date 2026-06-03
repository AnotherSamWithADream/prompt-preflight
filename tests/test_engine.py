"""Deterministic tests for the core engine.

These mock ``subprocess.run`` / the ``anthropic`` SDK so they never call the real
``claude`` binary or network: they verify command construction, the stdin (not argv)
prompt path, JSON-envelope parsing, the recursion guard, backend selection, the API
backend (incl. retry + prompt caching), and every fail-open path.
"""

import json
import subprocess
import sys
import types

from prompt_enhancer import engine
from prompt_enhancer.config import Config
from prompt_enhancer.engine import RECURSION_GUARD_ENV, build_command, enhance

LONG = "please could you help me improve and clarify this rough prompt for me today"


def _completed(result_text="REWRITTEN", returncode=0, is_error=False, stdout=None):
    if stdout is None:
        stdout = json.dumps(
            {"type": "result", "subtype": "success", "is_error": is_error, "result": result_text}
        )
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=""
    )


# --- command construction + stdin prompt ----------------------------------- #


def test_build_command_uses_verified_flags():
    cmd = build_command(model="haiku", max_turns="1")
    assert cmd[0] == engine.CLAUDE_BINARY
    assert "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--max-turns") + 1] == "1"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--system-prompt" in cmd
    assert "--append-system-prompt" not in cmd  # replace, not append


def test_prompt_goes_on_stdin_not_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _completed("REWRITTEN")

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    nasty = 'rm -rf / ; echo "$(whoami)" `id` && DROP TABLE users; --'
    enhance(nasty)

    # The prompt is passed on stdin, NEVER on the command line (no argv leak, no shell).
    assert nasty not in captured["cmd"]
    assert captured["kw"]["input"] == nasty
    assert captured["kw"]["env"][RECURSION_GUARD_ENV] == "1"


def test_recursion_guard_inherits_parent_env(monkeypatch):
    captured = {}
    monkeypatch.setenv("SOME_EXISTING_VAR", "keep-me")
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: captured.update(kw) or _completed("X")
    )
    enhance(LONG)
    assert captured["env"][RECURSION_GUARD_ENV] == "1"
    assert captured["env"]["SOME_EXISTING_VAR"] == "keep-me"


# --- JSON envelope parsing + success --------------------------------------- #


def test_success_returns_stripped_result(monkeypatch):
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: _completed("  Better, clearer prompt.\n")
    )
    r = enhance(LONG)
    assert r.enhanced is True
    assert r.text == "Better, clearer prompt."
    assert r.backend == "cli"


def test_parse_cli_json_variants():
    assert engine._parse_cli_json(json.dumps({"is_error": False, "result": "hi"})) == "hi"
    assert engine._parse_cli_json(json.dumps({"is_error": True, "result": "hi"})) is None
    assert engine._parse_cli_json("not json") is None
    assert engine._parse_cli_json("") is None
    assert engine._parse_cli_json(json.dumps({"result": 5})) is None


def test_fail_open_on_bad_json(monkeypatch):
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: _completed(stdout="this is not json", returncode=0),
    )
    r = enhance(LONG)
    assert r.enhanced is False
    assert r.text == LONG
    assert r.error == "bad-output"


def test_fail_open_on_empty_result(monkeypatch):
    monkeypatch.setattr(engine.subprocess, "run", lambda cmd, **kw: _completed("   "))
    r = enhance(LONG)
    assert r.enhanced is False
    assert r.error == "empty-output"


# --- fail-open paths -------------------------------------------------------- #


def test_fail_open_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: _completed("partial", returncode=1)
    )
    r = enhance(LONG)
    assert r.enhanced is False and r.text == LONG and r.error == "exit-1"


def test_fail_open_on_timeout(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(engine.subprocess, "run", boom)
    r = enhance(LONG)
    assert r.enhanced is False and r.text == LONG and r.error == "timeout"


def test_fail_open_when_binary_missing(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("claude not on PATH")

    monkeypatch.setattr(engine.subprocess, "run", boom)
    r = enhance(LONG)
    assert r.enhanced is False and r.error.startswith("spawn-failed")


def test_empty_input_never_spawns(monkeypatch):
    calls = {"n": 0}

    def fake(cmd, **kw):
        calls["n"] += 1
        return _completed("x")

    monkeypatch.setattr(engine.subprocess, "run", fake)
    r = enhance("   ")
    assert r.enhanced is False and r.error == "empty-input" and calls["n"] == 0


def test_too_long_skips(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: calls.__setitem__("n", calls["n"] + 1) or _completed("x"),
    )
    r = enhance("x" * 200_000, config=Config())
    assert r.enhanced is False and r.error == "too-long" and calls["n"] == 0


def test_custom_timeout_is_passed_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: captured.update(kw) or _completed("ok")
    )
    enhance(LONG, timeout=3.5)
    assert captured["timeout"] == 3.5


# --- claude binary resolution ---------------------------------------------- #


def test_resolve_binary_posix_uses_which(monkeypatch):
    monkeypatch.setattr(engine.os, "name", "posix")
    monkeypatch.setattr(engine.shutil, "which", lambda n: "/usr/local/bin/claude")
    assert engine.resolve_claude_binary() == "/usr/local/bin/claude"


def test_resolve_binary_windows_shim_to_bundled_exe(monkeypatch):
    monkeypatch.setattr(engine.os, "name", "nt")
    monkeypatch.setattr(
        engine.shutil, "which", lambda n: r"C:\Users\me\AppData\Roaming\npm\claude.CMD"
    )
    monkeypatch.setattr(engine.os.path, "isabs", lambda p: False)
    monkeypatch.setattr(engine.os.path, "isfile", lambda p: p.endswith("claude.exe"))
    got = engine.resolve_claude_binary()
    assert got.endswith("claude.exe") and "claude-code" in got


def test_resolve_binary_windows_real_exe_used_directly(monkeypatch):
    monkeypatch.setattr(engine.os, "name", "nt")
    monkeypatch.setattr(engine.shutil, "which", lambda n: r"C:\Program Files\claude\claude.exe")
    assert engine.resolve_claude_binary() == r"C:\Program Files\claude\claude.exe"


def test_resolve_binary_absolute_override(monkeypatch, tmp_path):
    exe = tmp_path / "my-claude"
    exe.write_text("x")
    monkeypatch.setenv("PROMPT_ENHANCER_CLAUDE_BIN", str(exe))
    assert engine.resolve_claude_binary() == str(exe)


def test_resolve_binary_missing_returns_name(monkeypatch):
    monkeypatch.setattr(engine.shutil, "which", lambda n: None)
    assert engine.resolve_claude_binary() == "claude"


# --- backend selection + API backend --------------------------------------- #


def test_auto_backend_picks_cli_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert engine._select_backend("auto", Config()) == "cli"


def test_auto_backend_picks_api_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert engine._select_backend("auto", Config()) == "api"


def test_api_backend_no_key_fails_open(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = enhance(LONG, backend="api", config=Config())
    assert r.enhanced is False and r.error == "no-api-key"


def test_api_backend_not_installed_fails_open(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    r = enhance(LONG, backend="api", config=Config())
    assert r.enhanced is False and r.error == "anthropic-not-installed"


def _fake_anthropic(create):
    class _Messages:
        def create(self, **kw):
            return create(**kw)

    class _Anthropic:
        def __init__(self, **kw):
            _Anthropic.init_kwargs = kw
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Anthropic
    return mod, _Anthropic


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


def test_api_backend_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def create(**kw):
        captured.update(kw)
        return _Message("REWRITTEN BY API")

    mod, cls = _fake_anthropic(create)
    monkeypatch.setitem(sys.modules, "anthropic", mod)

    cfg = Config()
    r = enhance(LONG, backend="api", config=cfg)
    assert r.enhanced and r.text == "REWRITTEN BY API" and r.backend == "api"
    assert captured["model"] == cfg.api_model
    system = captured["system"]
    assert isinstance(system, list)
    assert any("rewrite" in b["text"].lower() for b in system)
    assert any(b.get("cache_control") for b in system)  # prompt caching enabled
    assert cls.init_kwargs["base_url"] == cfg.upstream_base  # never the proxy


def test_api_backend_retries_transient(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class RateLimitError(Exception):
        pass

    rewritten = "Improve and clarify this rough prompt for a stronger model."

    def create(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("slow down")
        return _Message(rewritten)

    mod, _cls = _fake_anthropic(create)
    monkeypatch.setitem(sys.modules, "anthropic", mod)

    r = enhance(LONG, backend="api", config=Config())
    assert r.enhanced and r.text == rewritten and calls["n"] == 2


def test_cli_backend_drops_proxy_base_url(monkeypatch):
    captured = {}
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8788")
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: captured.update(kw) or _completed("ok")
    )
    enhance(LONG, backend="cli", config=Config())
    assert "ANTHROPIC_BASE_URL" not in captured["env"]


def test_cli_backend_keeps_foreign_base_url(monkeypatch):
    captured = {}
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: captured.update(kw) or _completed("ok")
    )
    enhance(LONG, backend="cli", config=Config())
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "https://gateway.example.com"


# --- quality + safety guards ----------------------------------------------- #

PLAUSIBLE = "Optimize the code and add comprehensive tests for it, please, right now."


def test_secret_detected_skips_backend(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: calls.__setitem__("n", calls["n"] + 1) or _completed(PLAUSIBLE),
    )
    r = enhance("please use my key sk-abcdefghijklmnopqrstuvwxyz12345 in the code", config=Config())
    assert r.enhanced is False and r.error.startswith("secret-detected") and calls["n"] == 0


def test_kill_switch_disables(monkeypatch):
    monkeypatch.setenv("PROMPT_ENHANCER_DISABLE", "1")
    r = enhance(LONG, config=Config())
    assert r.enhanced is False and r.error == "disabled"


def test_faithfulness_fail_open(monkeypatch):
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: _completed(
            "A clearer prompt about something entirely different here now."
        ),
    )
    r = enhance(
        "refactor utils.py and read https://example.com/spec carefully for me now", config=Config()
    )
    assert r.enhanced is False and r.error == "faithfulness"


def test_clean_output_strips_fences(monkeypatch):
    monkeypatch.setattr(
        engine.subprocess, "run", lambda cmd, **kw: _completed(f"```\n{PLAUSIBLE}\n```")
    )
    r = enhance(LONG, config=Config())
    assert r.enhanced is True and r.text == PLAUSIBLE


def test_cli_surfaces_cost_and_usage(monkeypatch):
    stdout = json.dumps(
        {
            "is_error": False,
            "result": PLAUSIBLE,
            "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 10, "output_tokens": 20, "x": 1},
        }
    )
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(["claude"], 0, stdout, ""),
    )
    r = enhance(LONG, config=Config())
    assert r.enhanced and r.cost_usd == 0.0123
    assert r.usage == {"input_tokens": 10, "output_tokens": 20}


def test_cli_bare_flag_in_command():
    cmd = build_command(bare=True)
    assert "--bare" in cmd
    assert "--bare" not in build_command(bare=False)


def test_cache_results_memoizes(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: calls.__setitem__("n", calls["n"] + 1) or _completed(PLAUSIBLE),
    )
    cfg = Config()
    cfg.cache_results = True
    r1 = enhance(LONG, config=cfg)
    r2 = enhance(LONG, config=cfg)
    assert r1.enhanced and r2.enhanced and calls["n"] == 1  # second served from cache


def test_circuit_breaker_opens(monkeypatch):
    cfg = Config()
    cfg.circuit_breaker_threshold = 3
    monkeypatch.setattr(engine.subprocess, "run", lambda cmd, **kw: _completed("x", returncode=1))
    for _ in range(3):
        enhance(LONG, config=cfg)
    calls = {"n": 0}
    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda cmd, **kw: calls.__setitem__("n", calls["n"] + 1) or _completed("x", returncode=1),
    )
    r = enhance(LONG, config=cfg)
    assert r.error == "circuit-open" and calls["n"] == 0


def test_profile_affects_system_prompt():
    from prompt_enhancer.system_prompt import ENHANCER_SYSTEM_PROMPT, system_prompt_for

    assert "PROFILE (coding)" in system_prompt_for("coding")
    assert system_prompt_for("default") == ENHANCER_SYSTEM_PROMPT


def test_openai_backend_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured = {}

    class _M:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _M(c)

    class _Resp:
        def __init__(self, c):
            self.choices = [_Choice(c)]

    class _Completions:
        def create(self, **kw):
            captured.update(kw)
            return _Resp(PLAUSIBLE)

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        def __init__(self, **kw):
            captured["init"] = kw
            self.chat = _Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = _OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    r = enhance(LONG, backend="openai", config=Config())
    assert r.enhanced and r.backend == "openai" and r.text == PLAUSIBLE
    assert captured["model"] == "gpt-4o-mini"


def test_ollama_backend_success(monkeypatch):
    import urllib.request

    class _Resp:
        def __init__(self, data):
            self._d = data

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = json.dumps({"message": {"content": PLAUSIBLE}}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp(payload))
    r = enhance(LONG, backend="ollama", config=Config())
    assert r.enhanced and r.backend == "ollama" and r.text == PLAUSIBLE


# --- heuristic (no-LLM) backend -------------------------------------------- #


def test_heuristic_backend_normalizes_without_llm(monkeypatch):
    # Must never shell out: blow up if it tries to spawn the CLI.
    monkeypatch.setattr(
        engine.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned"))
    )
    r = enhance("fix   the\n\n\n\nbug  in  parser", backend="heuristic", config=Config())
    assert r.enhanced and r.backend == "heuristic"
    assert r.text.startswith("Fix the")  # capitalised + whitespace collapsed
    assert "\n\n\n" not in r.text


def test_heuristic_preserves_all_tokens(monkeypatch):
    raw = "use file config.py and url https://x.io now"
    r = enhance(raw, backend="heuristic", config=Config())
    assert r.enhanced
    for tok in ("config.py", "https://x.io"):
        assert tok in r.text  # faithfulness guard must pass


def test_auto_falls_back_to_heuristic_without_cli_or_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)  # no claude binary
    monkeypatch.setattr(engine, "_claude_available", lambda: False)
    r = enhance(LONG, backend="auto", config=Config())
    assert r.enhanced and r.backend == "heuristic"


# --- plugin (entry-point) backends ----------------------------------------- #


def test_plugin_backend_dispatch(monkeypatch):
    def _fake_plugin(raw_prompt, cfg, *, start):
        return engine.EnhanceResult(PLAUSIBLE, True, raw_prompt, backend="myplugin")

    monkeypatch.setattr(engine, "find_plugin_backend", lambda name: _fake_plugin)
    r = enhance(LONG, backend="myplugin", config=Config())
    assert r.enhanced and r.backend == "myplugin" and r.text == PLAUSIBLE


def test_plugin_backend_unknown_fails_open(monkeypatch):
    monkeypatch.setattr(engine, "find_plugin_backend", lambda name: None)
    # Force the dispatch into the plugin branch by treating the name as already selected.
    monkeypatch.setattr(engine, "_select_backend", lambda backend, cfg: "nope")
    r = enhance(LONG, backend="nope", config=Config())
    assert r.enhanced is False and r.error.startswith("unknown-backend")


def test_plugin_backend_crash_fails_open(monkeypatch):
    def _boom(raw_prompt, cfg, *, start):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(engine, "find_plugin_backend", lambda name: _boom)
    r = enhance(LONG, backend="myplugin", config=Config())
    assert r.enhanced is False and r.error.startswith("plugin-error")


# --- Bedrock / Vertex providers -------------------------------------------- #


def test_api_provider_bedrock_uses_bedrock_client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    captured = {}

    class _Msg:
        content = [types.SimpleNamespace(type="text", text=PLAUSIBLE)]
        usage = types.SimpleNamespace(input_tokens=5, output_tokens=9)

    class _Messages:
        def create(self, **kw):
            return _Msg()

    class _Bedrock:
        def __init__(self, **kw):
            captured["bedrock"] = kw
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.AnthropicBedrock = _Bedrock
    mod.Anthropic = lambda **kw: (_ for _ in ()).throw(AssertionError("used direct client"))
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    cfg = Config()
    cfg.api_provider = "bedrock"
    r = enhance(LONG, backend="api", config=cfg)
    assert r.enhanced and r.backend == "api" and "bedrock" in captured

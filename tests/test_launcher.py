"""Tests for the ``enhance`` launcher (enhance first prompt + proxy + claude).

The server, engine, and claude subprocess are faked, so nothing binds a port, calls
claude, or hits the network."""

from prompt_enhancer import launcher
from prompt_enhancer.engine import EnhanceResult


class _FakeServer:
    def __init__(self, host="127.0.0.1", port=8788):
        self.server_address = (host, port)
        self.shutdown_called = False
        self.closed = False

    def serve_forever(self):
        pass

    def shutdown(self):
        self.shutdown_called = True

    def server_close(self):
        self.closed = True


def _fake_make_server(fake, store):
    def make(cfg, skip_texts=None):
        store["skip"] = skip_texts
        return fake

    return make


def _capture_run(store):
    def run(cmd, **kw):
        store["cmd"] = cmd
        store["env"] = kw.get("env")

        class _R:
            returncode = 0

        return _R()

    return run


def _record(store, key):
    def fn(value):
        store[key] = value
        return 0

    return fn


# --- argument parsing ------------------------------------------------------- #


def test_parse_invocation_prompt_only():
    assert launcher.parse_invocation(["make", "my", "code", "faster"]) == (
        False,
        None,
        "make my code faster",
        [],
    )


def test_parse_invocation_prompt_then_flags():
    assert launcher.parse_invocation(["fix the bug", "--model", "opus"]) == (
        False,
        None,
        "fix the bug",
        ["--model", "opus"],
    )


def test_parse_invocation_flags_only():
    assert launcher.parse_invocation(["--model", "opus"]) == (False, None, "", ["--model", "opus"])


def test_parse_invocation_double_dash_passthrough():
    # Everything after `--` goes to claude verbatim; leading words remain the prompt.
    assert launcher.parse_invocation(["fix the bug", "--", "--model", "opus", "-c"]) == (
        False,
        None,
        "fix the bug",
        ["--model", "opus", "-c"],
    )


def test_parse_invocation_message_passthrough_no_steal():
    # A `-m` AFTER `--` is a claude arg, never mistaken for the launcher's own -m.
    assert launcher.parse_invocation(["-m", "do it", "--", "-m", "claude-flag"]) == (
        False,
        "do it",
        "do it",
        ["-m", "claude-flag"],
    )


def test_parse_invocation_launcher_opts_are_leading_only():
    # A trailing -q is forwarded to claude, not consumed as the launcher's --quiet.
    assert launcher.parse_invocation(["fix it now", "-q"]) == (False, None, "fix it now", ["-q"])


def test_parse_invocation_quiet_and_message():
    assert launcher.parse_invocation(["-q", "-m", "hi", "--model", "opus"]) == (
        True,
        "hi",
        "hi",
        ["--model", "opus"],
    )


def test_parse_invocation_empty():
    assert launcher.parse_invocation([]) == (False, None, "", [])


# --- env wiring ------------------------------------------------------------- #


def test_build_child_env_points_at_proxy():
    env, base = launcher.build_child_env({"PATH": "x"}, "127.0.0.1", 9999)
    assert base == "http://127.0.0.1:9999"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert env["PROMPT_ENHANCER_PROXY_HOST"] == "127.0.0.1"
    assert env["PROMPT_ENHANCER_PROXY_PORT"] == "9999"
    assert env["PATH"] == "x"


# --- launch behavior -------------------------------------------------------- #


def test_launch_enhances_initial_prompt_and_skips_it(monkeypatch):
    fake = _FakeServer(port=12345)
    store, run = {}, {}
    monkeypatch.setattr(launcher, "make_server", _fake_make_server(fake, store))
    monkeypatch.setattr(launcher, "resolve_claude_binary", lambda: "claude-bin")
    monkeypatch.setattr(launcher, "_interactive_capable", lambda: True)
    monkeypatch.setattr(
        launcher, "enhance", lambda text, **k: EnhanceResult("ENHANCED PROMPT", True, text)
    )
    monkeypatch.setattr(launcher.subprocess, "run", _capture_run(run))

    code = launcher.launch(["make", "my", "code", "much", "faster", "please"])
    assert code == 0
    # interactive claude launched with the ENHANCED prompt as its initial message
    assert run["cmd"] == ["claude-bin", "ENHANCED PROMPT"]
    assert run["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:12345"
    # and the enhanced text is registered so the proxy won't enhance it again
    assert "ENHANCED PROMPT" in store["skip"]
    assert fake.shutdown_called and fake.closed


def test_launch_passes_flags_without_prompt(monkeypatch):
    fake = _FakeServer(port=999)
    store, run = {}, {}
    monkeypatch.setattr(launcher, "make_server", _fake_make_server(fake, store))
    monkeypatch.setattr(launcher, "resolve_claude_binary", lambda: "claude-bin")
    monkeypatch.setattr(launcher, "_interactive_capable", lambda: True)

    def _no_enhance(*a, **k):
        raise AssertionError("no prompt -> enhance must not be called")

    monkeypatch.setattr(launcher, "enhance", _no_enhance)
    monkeypatch.setattr(launcher.subprocess, "run", _capture_run(run))

    code = launcher.launch(["--model", "opus", "-c"])
    assert code == 0
    assert run["cmd"] == ["claude-bin", "--model", "opus", "-c"]  # no initial prompt appended
    assert store["skip"] == set()


def test_launch_raw_prefix_skips_enhancement(monkeypatch):
    fake = _FakeServer()
    store, run = {}, {}
    monkeypatch.setattr(launcher, "make_server", _fake_make_server(fake, store))
    monkeypatch.setattr(launcher, "resolve_claude_binary", lambda: "claude-bin")
    monkeypatch.setattr(launcher, "_interactive_capable", lambda: True)

    def _no_enhance(*a, **k):
        raise AssertionError("//raw must not enhance")

    monkeypatch.setattr(launcher, "enhance", _no_enhance)
    monkeypatch.setattr(launcher.subprocess, "run", _capture_run(run))

    code = launcher.launch(["//raw", "do", "exactly", "this", "please"])
    assert code == 0
    assert run["cmd"] == ["claude-bin", "do exactly this please"]  # token stripped, not enhanced


def test_launch_handles_missing_claude(monkeypatch):
    fake = _FakeServer()
    store = {}
    monkeypatch.setattr(launcher, "make_server", _fake_make_server(fake, store))
    monkeypatch.setattr(launcher, "_resolve_claude_binary", lambda: "nope")

    def boom(cmd, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(launcher.subprocess, "run", boom)
    assert launcher.launch(["--model", "opus"]) == 127
    assert fake.shutdown_called


def test_main_serve_only_dispatches_to_proxy(monkeypatch):
    import prompt_enhancer.proxy as proxymod

    called = {}
    monkeypatch.setattr(proxymod, "main", _record(called, "argv"))
    assert launcher.main(["--serve-only", "--port", "9000"]) == 0
    assert called["argv"] == ["--port", "9000"]


def test_main_forwards_remaining_args_to_launch(monkeypatch):
    captured = {}
    monkeypatch.setattr(launcher, "launch", _record(captured, "args"))
    assert launcher.main(["fix the bug", "--model", "opus"]) == 0
    assert captured["args"] == ["fix the bug", "--model", "opus"]


def test_parse_invocation_message_equals_form():
    assert launcher.parse_invocation(["--message=do it", "--model", "opus"]) == (
        False,
        "do it",
        "do it",
        ["--model", "opus"],
    )


def test_launch_prepends_config_claude_args(monkeypatch):
    from prompt_enhancer.config import Config

    fake = _FakeServer(port=55)
    store, run = {}, {}
    cfg = Config()
    cfg.claude_args = ("--model", "opus")
    monkeypatch.setattr(launcher, "load_config", lambda: cfg)
    monkeypatch.setattr(launcher, "make_server", _fake_make_server(fake, store))
    monkeypatch.setattr(launcher, "resolve_claude_binary", lambda: "claude-bin")
    monkeypatch.setattr(launcher, "_interactive_capable", lambda: True)

    def _no_enhance(*a, **k):
        raise AssertionError("no prompt -> enhance must not be called")

    monkeypatch.setattr(launcher, "enhance", _no_enhance)
    monkeypatch.setattr(launcher.subprocess, "run", _capture_run(run))

    assert launcher.launch(["--", "-c"]) == 0
    # config defaults come first, CLI passthrough args appended (so CLI wins)
    assert run["cmd"] == ["claude-bin", "--model", "opus", "-c"]


def test_launch_non_tty_adds_print_mode(monkeypatch):
    fake = _FakeServer(port=7)
    store, run = {}, {}
    monkeypatch.setattr(launcher, "make_server", _fake_make_server(fake, store))
    monkeypatch.setattr(launcher, "resolve_claude_binary", lambda: "claude-bin")
    monkeypatch.setattr(launcher, "_interactive_capable", lambda: False)  # piped stdout
    monkeypatch.setattr(
        launcher, "enhance", lambda text, **k: EnhanceResult("ENHANCED", True, text)
    )
    monkeypatch.setattr(launcher.subprocess, "run", _capture_run(run))
    assert launcher.launch(["make", "this", "much", "clearer", "for", "the", "model"]) == 0
    assert "-p" in run["cmd"]
    assert run["cmd"][-1] == "ENHANCED"


def test_main_version(capsys):
    assert launcher.main(["--version"]) == 0
    assert "prompt-preflight" in capsys.readouterr().out


def test_arg_parsing_never_raises():
    for args in (
        [],
        ["-"],
        ["--"],
        ["--", ""],
        ["-x", "-y"],
        ["a", "-b", "c"],
        ["--message"],
        ["-m"],
        ["-q", "-m"],
        ["--message="],
        ["fix", "--", "-m"],
    ):
        quiet, message, prompt, claude_args = launcher.parse_invocation(args)
        assert isinstance(prompt, str) and isinstance(claude_args, list) and isinstance(quiet, bool)

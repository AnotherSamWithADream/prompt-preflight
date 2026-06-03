"""Tests for the ``enhance`` CLI.

The engine and clipboard are mocked, so these are deterministic and never call
``claude`` or touch the real clipboard. They cover the //raw bypass, accept/reject/
fail-open routing, the change summary, and clipboard selection.
"""

import json
import subprocess

from prompt_enhancer import cli
from prompt_enhancer.engine import EnhanceResult


def _capture_clipboard(monkeypatch):
    box = {}
    monkeypatch.setattr(cli, "copy_to_clipboard", lambda t: box.update(text=t) or True)
    return box


def test_strip_raw_prefix_variants():
    assert cli.strip_raw_prefix("//raw hello") == ("hello", True)
    assert cli.strip_raw_prefix("//raw\nhello") == ("hello", True)
    assert cli.strip_raw_prefix("  //raw  hi there") == ("hi there", True)
    assert cli.strip_raw_prefix("//raw") == ("", True)
    # //raw not at the start is not a bypass.
    text, is_raw = cli.strip_raw_prefix("write a poem //raw style")
    assert is_raw is False and text == "write a poem //raw style"


def test_summary_reports_counts_and_open_questions():
    enhanced = (
        "Optimize the program to run faster and add unit tests.\n\n"
        "Open questions:\n- Which file?\n- Which language?"
    )
    s = cli.summarize_changes("make it faster", enhanced)
    assert "words" in s
    assert "Open questions" in s
    assert "2 items" in s


def test_clipboard_command_per_platform(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli._clipboard_command() == ["pbcopy"]
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli._clipboard_command() == ["clip"]


def test_copy_to_clipboard_pipes_text(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli.sys, "platform", "darwin"
    )  # take the subprocess path, not the Win32 API
    monkeypatch.setattr(cli, "_clipboard_command", lambda: ["pbcopy"])

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, input=kw.get("input"))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.copy_to_clipboard("hello world") is True
    assert seen["cmd"] == ["pbcopy"]
    assert seen["input"] == "hello world"


def test_raw_bypass_strips_and_copies(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("enhance must not be called for //raw")

    monkeypatch.setattr(cli, "enhance", _boom)
    box = _capture_clipboard(monkeypatch)
    code = cli.run(["//raw", "leave", "this", "exactly", "as", "is"])
    assert code == 0
    assert box["text"] == "leave this exactly as is"


def test_accept_copies_enhanced(monkeypatch):
    monkeypatch.setattr(
        cli, "enhance", lambda *a, **k: EnhanceResult("ENHANCED PROMPT", True, "orig")
    )
    box = _capture_clipboard(monkeypatch)
    code = cli.run(
        [
            "-y",
            "please",
            "improve",
            "this",
            "rough",
            "prompt",
            "for",
            "me",
            "thanks",
            "a",
            "lot",
            "today",
            "ok",
        ]
    )
    assert code == 0
    assert box["text"] == "ENHANCED PROMPT"


def test_fail_open_copies_original(monkeypatch):
    words = [
        "this",
        "prompt",
        "is",
        "long",
        "enough",
        "to",
        "attempt",
        "enhancement",
        "but",
        "the",
        "engine",
        "fails",
    ]
    original = " ".join(words)
    monkeypatch.setattr(
        cli, "enhance", lambda *a, **k: EnhanceResult(original, False, original, error="timeout")
    )
    box = _capture_clipboard(monkeypatch)
    code = cli.run(["-y"] + words)
    assert code == 0
    assert box["text"] == original


def test_reject_falls_back_to_original(monkeypatch):
    original_words = [
        "please",
        "make",
        "this",
        "much",
        "clearer",
        "and",
        "better",
        "for",
        "the",
        "stronger",
        "model",
        "thanks",
    ]
    original = " ".join(original_words)
    monkeypatch.setattr(cli, "enhance", lambda *a, **k: EnhanceResult("ENHANCED", True, original))
    monkeypatch.setattr(cli, "_choose", lambda enhanced: None)  # simulate Reject
    box = _capture_clipboard(monkeypatch)
    code = cli.run(original_words)
    assert code == 0
    assert box["text"] == original  # reject copies the user's original, not the rewrite


def test_no_input_returns_error(monkeypatch):
    monkeypatch.setattr(cli, "_read_input", lambda args: "")
    code = cli.run(["--no-clipboard"])
    assert code == 2


# --- new: --json, config set, doctor, Windows clipboard -------------------- #


def test_emit_json(capsys):
    assert cli._emit_json("orig prompt", "ENHANCED", None) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["original"] == "orig prompt"
    assert out["enhanced"] == "ENHANCED"
    assert out["changed"] is True


def test_run_json_mode(monkeypatch, capsys):
    monkeypatch.setattr(cli, "enhance", lambda *a, **k: EnhanceResult("ENHANCED", True, "orig"))
    code = cli.run(
        [
            "--json",
            "please",
            "make",
            "this",
            "much",
            "clearer",
            "for",
            "the",
            "model",
            "now",
            "thanks",
            "a",
            "lot",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["enhanced"] == "ENHANCED" and out["did_enhance"] is True


def test_config_set_writes_file(monkeypatch, tmp_path):
    cfgfile = tmp_path / "c.json"
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(cfgfile))
    assert cli.config_main(["set", "word_threshold", "5"]) == 0
    assert json.loads(cfgfile.read_text())["word_threshold"] == 5


def test_config_set_rejects_unknown_key(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(tmp_path / "c.json"))
    assert cli.config_main(["set", "bogus_key", "1"]) == 2


def test_doctor_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "enhance", lambda *a, **k: EnhanceResult("ENH", True, "orig prompt here")
    )
    monkeypatch.setattr(cli, "_claude_version", lambda b: "2.1.154 (Claude Code)")
    assert cli.doctor_main([]) == 0
    assert "doctor" in capsys.readouterr().err


def test_doctor_reports_fail_open(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "enhance", lambda *a, **k: EnhanceResult("orig", False, "orig", error="timeout")
    )
    monkeypatch.setattr(cli, "_claude_version", lambda b: "2.1.154")
    assert cli.doctor_main([]) == 1


def test_win_clipboard_used_on_windows(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(
        cli, "_win_set_clipboard", lambda t: calls.__setitem__("n", calls["n"] + 1) or True
    )
    assert cli.copy_to_clipboard("héllo 你好") is True
    assert calls["n"] == 1


def test_unified_diff():
    d = cli._unified_diff("a\nb", "a\nc")
    assert "-b" in d and "+c" in d


def test_config_path_and_show(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(tmp_path / "cfg.json"))
    assert cli.config_main(["path"]) == 0
    assert str(tmp_path / "cfg.json") in capsys.readouterr().out
    assert cli.config_main(["show"]) == 0
    assert "backend" in capsys.readouterr().out


def test_clipboard_unavailable_returns_false(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli, "_clipboard_command", lambda: None)
    assert cli.copy_to_clipboard("x") is False


# --- new flags + subcommands ----------------------------------------------- #

ENH = "Improve and clarify this rough prompt for a stronger model right now please."


def test_profile_flag_applied(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(
        cli,
        "enhance",
        lambda raw, config=None, **k: (
            captured.update(profile=config.profile) or EnhanceResult(ENH, True, raw)
        ),
    )
    cli.run(["--json", "--profile", "coding", "make", "this", "much", "clearer", "now", "please"])
    assert captured["profile"] == "coding"


def test_read_from_file(monkeypatch, tmp_path):
    p = tmp_path / "prompt.txt"
    p.write_text("please make this much clearer for the strong model now thanks")
    monkeypatch.setattr(cli, "enhance", lambda raw, **k: EnhanceResult(ENH, True, raw))
    box = _capture_clipboard(monkeypatch)
    assert cli.run(["-y", "-f", str(p)]) == 0
    assert box["text"] == ENH


def test_explain_prints_trace(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "enhance", lambda raw, **k: EnhanceResult(ENH, True, raw, backend="cli")
    )
    cli.run(["-y", "--explain", "make", "this", "much", "clearer", "now", "please", "thanks"])
    assert "backend=cli" in capsys.readouterr().err


def test_json_includes_cost(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "enhance",
        lambda raw, **k: EnhanceResult(
            ENH, True, raw, backend="cli", cost_usd=0.01, usage={"input_tokens": 5}
        ),
    )
    cli.run(["--json", "make", "this", "much", "clearer", "now", "please", "thanks"])
    out = json.loads(capsys.readouterr().out)
    assert out["cost_usd"] == 0.01 and out["usage"] == {"input_tokens": 5}


def test_config_unset(monkeypatch, tmp_path):
    cfgfile = tmp_path / "c.json"
    cfgfile.write_text(json.dumps({"backend": "api", "word_threshold": 5}))
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(cfgfile))
    assert cli.config_main(["unset", "backend"]) == 0
    data = json.loads(cfgfile.read_text())
    assert "backend" not in data and data["word_threshold"] == 5


def test_config_reset(monkeypatch, tmp_path):
    cfgfile = tmp_path / "c.json"
    cfgfile.write_text("{}")
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(cfgfile))
    assert cli.config_main(["reset"]) == 0
    assert not cfgfile.exists()


def test_init_registers_hook(tmp_path):
    settings = tmp_path / "settings.json"
    assert cli.init_main([str(settings)]) == 0
    data = json.loads(settings.read_text())
    assert "enhance-hook" in json.dumps(data["hooks"]["UserPromptSubmit"])
    assert cli.init_main([str(settings)]) == 0  # idempotent


def test_stats_reads_proxy(monkeypatch, capsys):
    import urllib.request

    class _R:
        def read(self):
            return json.dumps({"requests": 3, "rewrites": 1}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _R())
    assert cli.stats_main([]) == 0
    assert "requests" in capsys.readouterr().out


# --- REPL / watch / clarify ------------------------------------------------ #


def test_extract_open_questions():
    text = "Do the thing.\n\nOpen questions:\n- Which file?\n- Which language?\n\nEnd."
    assert cli._extract_open_questions(text) == ["Which file?", "Which language?"]
    assert cli._extract_open_questions("no questions here") == []


def test_repl_enhances_each_line(monkeypatch, capsys):
    lines = iter(["make this much clearer please now", ":q"])
    monkeypatch.setattr(cli, "_prompt_line", lambda msg: next(lines, ""))
    monkeypatch.setattr(cli, "enhance", lambda text, **k: EnhanceResult("REPL-OUT", True, text))
    assert cli.repl(cli.load_config()) == 0
    assert "REPL-OUT" in capsys.readouterr().out


def test_clarify_refines_with_answers(monkeypatch, capsys):
    enhanced = "Build it.\n\nOpen questions:\n- Which framework?"
    answers = iter(["y", "React"])
    monkeypatch.setattr(cli, "_prompt_line", lambda msg: next(answers, ""))
    seen = {}

    def fake_enhance(raw, **k):
        seen["raw"] = raw
        return EnhanceResult("REFINED", True, raw)

    monkeypatch.setattr(cli, "enhance", fake_enhance)
    raw2, enhanced2 = cli._maybe_clarify("build an app", enhanced, cli.load_config())
    assert enhanced2 == "REFINED"
    assert "Additional context:" in raw2 and "React" in raw2


def test_clarify_noop_without_questions():
    raw, enhanced = cli._maybe_clarify("orig", "no open questions in this rewrite", None)
    assert (raw, enhanced) == ("orig", "no open questions in this rewrite")


def test_reject_after_clarify_restores_true_original(monkeypatch):
    # After answering open questions (which appends an "Additional context" block) the user
    # rejects: the clipboard must get the TRUE original, never original + injected Q&A.
    box = _capture_clipboard(monkeypatch)
    with_questions = "Build it.\n\nOpen questions:\n- Which framework?"

    def fake_enhance(raw, **k):
        text = "REFINED" if "Additional context" in raw else with_questions
        return EnhanceResult(text, True, raw)

    monkeypatch.setattr(cli, "enhance", fake_enhance)
    answers = iter(["y", "React", "r"])  # answer prompt, the question, then [R]eject
    monkeypatch.setattr(cli, "_prompt_line", lambda msg: next(answers, ""))
    original = "build an app with good structure and clear modules please now thanks"
    cli.run(original.split())
    assert box["text"] == original  # not original + "Additional context: ..."


def test_watch_requires_clipboard_reader(monkeypatch):
    monkeypatch.setattr(cli, "_read_clipboard", lambda: None)
    assert cli.watch(cli.load_config()) == 1


def test_read_clipboard_darwin(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")

    def fake_run(cmd, **kw):
        assert cmd == ["pbpaste"]
        return subprocess.CompletedProcess(cmd, 0, stdout="clip text")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._read_clipboard() == "clip text"


def test_watch_enhances_changed_clipboard(monkeypatch):
    import time

    new_prompt = "please make this prompt much clearer and better for the strong model now thanks"
    # availability check, initial last, the new prompt, then our own echoed write.
    reads = iter(["x", "x", new_prompt, new_prompt.upper()])

    def fake_read():
        try:
            return next(reads)
        except StopIteration as exc:  # next loop tick: stop cleanly
            raise KeyboardInterrupt from exc

    monkeypatch.setattr(cli, "_read_clipboard", fake_read)
    monkeypatch.setattr(cli, "enhance", lambda text, **k: EnhanceResult(text.upper(), True, text))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    box = {}
    monkeypatch.setattr(cli, "copy_to_clipboard", lambda t: box.update(text=t) or True)
    assert cli.watch(cli.load_config()) == 0
    assert box.get("text") == new_prompt.upper()  # enhanced text written back to clipboard


def test_run_dispatches_repl(monkeypatch):
    called = {}

    def fake_repl(cfg):
        called["repl"] = True
        return 0

    monkeypatch.setattr(cli, "repl", fake_repl)
    assert cli.run(["--repl"]) == 0
    assert called.get("repl")

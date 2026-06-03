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

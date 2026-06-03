"""Tests for the UserPromptSubmit hook.

The engine is mocked at the ``hook.enhance`` seam, so these are deterministic and do
not call ``claude``. They cover: the recursion guard, the //raw bypass, slash-command
and short-prompt skipping, the enhanced -> additionalContext path, fail-open, and
malformed input.
"""

import io
import json
import sys

from prompt_enhancer import hook
from prompt_enhancer.engine import RECURSION_GUARD_ENV, EnhanceResult


def _must_not_call(*args, **kwargs):
    raise AssertionError("enhance() should NOT have been called for this input")


def _run_hook(monkeypatch, prompt, *, raw_stdin=None, env=None):
    stdin_text = (
        raw_stdin
        if raw_stdin is not None
        else json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    code = hook.main()
    return code, out.getvalue()


def test_recursion_guard_passes_through(monkeypatch):
    # If the guard is set, the hook must do nothing -- and must not invoke the engine.
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(
        monkeypatch,
        "a genuinely long prompt with plenty of words to be enhanced normally here",
        env={RECURSION_GUARD_ENV: "1"},
    )
    assert code == 0
    assert out == ""  # nothing emitted -> the prompt is untouched


def test_long_prompt_becomes_additional_context(monkeypatch):
    monkeypatch.setattr(
        hook, "enhance", lambda *a, **k: EnhanceResult("CLARIFIED VERSION", True, "orig")
    )
    code, out = _run_hook(
        monkeypatch,
        "please could you help me improve the performance of my data pipeline significantly",
    )
    assert code == 0
    data = json.loads(out)
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "CLARIFIED VERSION" in hso["additionalContext"]
    assert "Clarified restatement" in hso["additionalContext"]
    # No prompt-replacement field exists in the contract, so we must not emit one.
    assert "updatedPrompt" not in out


def test_raw_bypass_skips_enhancement(monkeypatch):
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(
        monkeypatch, "//raw do exactly what i say without any changes at all please now"
    )
    assert code == 0
    assert out == ""


def test_slash_command_is_skipped(monkeypatch):
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(
        monkeypatch, "/review please take a careful look at this code and tell me what is wrong"
    )
    assert code == 0
    assert out == ""


def test_short_prompt_is_skipped(monkeypatch):
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(monkeypatch, "fix the failing test")  # 4 words
    assert code == 0
    assert out == ""


def test_fail_open_emits_nothing(monkeypatch):
    monkeypatch.setattr(
        hook, "enhance", lambda *a, **k: EnhanceResult("orig", False, "orig", error="timeout")
    )
    code, out = _run_hook(
        monkeypatch,
        "a sufficiently long prompt that triggers enhancement but the engine fails open now",
    )
    assert code == 0
    assert out == ""


def test_malformed_json_passes_through(monkeypatch):
    code, out = _run_hook(monkeypatch, prompt=None, raw_stdin="this is not valid json {")
    assert code == 0
    assert out == ""


def test_missing_prompt_field_passes_through(monkeypatch):
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(
        monkeypatch, prompt=None, raw_stdin=json.dumps({"hook_event_name": "UserPromptSubmit"})
    )
    assert code == 0
    assert out == ""


def test_proxy_active_disables_hook(monkeypatch):
    # When ANTHROPIC_BASE_URL points at our proxy, the hook must step aside.
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(
        monkeypatch,
        "a long prompt with plenty of words that would normally be enhanced here now",
        env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:8788"},
    )
    assert code == 0
    assert out == ""


def test_proxy_nondefault_port_disables_hook(monkeypatch):
    # Even on a non-default port, a loopback base URL disables the hook (fix #51).
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    code, out = _run_hook(
        monkeypatch,
        "a long prompt with plenty of words that would normally be enhanced here now",
        env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"},
    )
    assert code == 0 and out == ""


def test_plan_mode_is_skipped(monkeypatch):
    monkeypatch.setattr(hook, "enhance", _must_not_call)
    prompt = "a long prompt with plenty of words that would normally be enhanced here now"
    code, out = _run_hook(
        monkeypatch,
        prompt,
        raw_stdin=json.dumps(
            {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": prompt}
        ),
    )
    assert code == 0 and out == ""


def test_decide_is_pure():
    # The exact word-count boundary: 11 words skipped, 12 enhanced.
    eleven = " ".join(["word"] * 11)
    assert hook.decide(eleven) is None


def test_minimal_output_style(monkeypatch):
    from prompt_enhancer.config import Config

    cfg = Config()
    cfg.hook_output_style = "minimal"
    monkeypatch.setattr(hook, "enhance", lambda *a, **k: EnhanceResult("CLARIFIED", True, "orig"))
    ctx = hook.decide(" ".join(["word"] * 12), cfg)
    assert ctx is not None
    assert "CLARIFIED" in ctx
    assert "begin clarified restatement" not in ctx  # the verbose block is suppressed
    assert ctx.count("\n") == 0  # single line

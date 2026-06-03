"""Tests for the proxy's request-body rewriting -- the part that decides what the
strong model actually sees. The engine is mocked, so no network / no claude.

Mirrors what Claude Code actually sends (observed live): the main turn carries a
`tools` list and packs the prompt alongside a `<system-reminder>` text block;
background/title calls have no tools; tool-loop turns end in a `tool_result`.
"""

import json

from prompt_enhancer import proxy
from prompt_enhancer.config import Config
from prompt_enhancer.engine import EnhanceResult

OPUS = "claude-opus-4-8"
TOOLS = [{"name": "Read", "input_schema": {"type": "object"}}]
REMINDER = {"type": "text", "text": "<system-reminder>\nSome injected context.\n</system-reminder>"}
LONG = "please optimize this module for performance and add tests across the whole codebase now"


def _body(payload):
    return json.dumps(payload).encode("utf-8")


def _no_enhance(*a, **k):
    raise AssertionError("enhance() should not have been called")


def _main_call(content):
    """A request shaped like Claude Code's main agentic turn."""
    return {"model": OPUS, "tools": TOOLS, "messages": [{"role": "user", "content": content}]}


# --- path + extraction ----------------------------------------------------- #


def test_is_messages_path():
    assert proxy._is_messages_path("/v1/messages")
    assert proxy._is_messages_path("/v1/messages?beta=true")
    assert not proxy._is_messages_path("/v1/models")


def test_extract_string_content():
    msg = {"role": "user", "content": "hello there"}
    text, setter = proxy._extract_user_prompt(msg)
    assert text == "hello there"
    setter("NEW")
    assert msg["content"] == "NEW"


def test_extract_single_text_block():
    msg = {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    text, setter = proxy._extract_user_prompt(msg)
    assert text == "hi"
    setter("X")
    assert msg["content"][0]["text"] == "X"


def test_extract_selects_human_block_among_reminders():
    human = {"type": "text", "text": LONG}
    msg = {"role": "user", "content": [REMINDER, human]}
    text, setter = proxy._extract_user_prompt(msg)
    assert text == LONG  # picked the non-reminder block
    setter("ENHANCED")
    assert human["text"] == "ENHANCED"
    assert msg["content"][0] is REMINDER  # reminder untouched


def test_extract_rejects_tool_result():
    msg = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "x", "content": "42"}],
    }
    assert proxy._extract_user_prompt(msg) == (None, None)


def test_extract_rejects_image_block():
    msg = {
        "role": "user",
        "content": [{"type": "text", "text": "a"}, {"type": "image", "source": {}}],
    }
    assert proxy._extract_user_prompt(msg)[0] is None


# --- full rewrite path ----------------------------------------------------- #


def test_rewrite_enhances_main_call(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", lambda text, **k: EnhanceResult("ENHANCED", True, text))
    new, did = proxy.rewrite_request_body(_body(_main_call(LONG)), Config())
    assert did
    assert json.loads(new)["messages"][-1]["content"] == "ENHANCED"


def test_rewrite_enhances_human_block_only(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", lambda text, **k: EnhanceResult("ENHANCED", True, text))
    payload = _main_call([REMINDER, {"type": "text", "text": LONG}])
    new, did = proxy.rewrite_request_body(_body(payload), Config())
    assert did
    blocks = json.loads(new)["messages"][-1]["content"]
    assert blocks[0]["text"].startswith("<system-reminder>")  # reminder preserved
    assert blocks[1]["text"] == "ENHANCED"  # only the human block changed


def test_rewrite_skips_without_tools(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)
    payload = {"model": OPUS, "messages": [{"role": "user", "content": LONG}]}  # no tools
    assert proxy.rewrite_request_body(_body(payload), Config())[1] is False


def test_rewrite_skips_haiku_model(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)
    payload = {
        "model": "claude-haiku-4-5",
        "tools": TOOLS,
        "messages": [{"role": "user", "content": LONG}],
    }
    assert proxy.rewrite_request_body(_body(payload), Config())[1] is False


def test_rewrite_skips_tool_result_turn(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)
    payload = _main_call([{"type": "tool_result", "tool_use_id": "x", "content": "42"}])
    assert proxy.rewrite_request_body(_body(payload), Config())[1] is False


def test_rewrite_strips_raw_token(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)  # //raw must not call the engine
    text = "//raw keep this exact text without any changes at all please thanks a lot"
    new, did = proxy.rewrite_request_body(_body(_main_call(text)), Config())
    assert did
    assert json.loads(new)["messages"][-1]["content"] == text[len("//raw ") :]


def test_rewrite_skips_short_prompt(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)
    assert proxy.rewrite_request_body(_body(_main_call("fix it now")), Config())[1] is False


def test_rewrite_skips_already_enhanced(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)  # launcher already enhanced it
    new, did = proxy.rewrite_request_body(
        _body(_main_call(LONG)), Config(), skip_texts={LONG.strip()}
    )
    assert not did


def test_rewrite_passthrough_on_nonjson():
    new, did = proxy.rewrite_request_body(b"this is not json", Config())
    assert not did
    assert new == b"this is not json"


def test_rewrite_failopen_keeps_body(monkeypatch):
    monkeypatch.setattr(
        proxy, "enhance", lambda text, **k: EnhanceResult(text, False, text, error="timeout")
    )
    new, did = proxy.rewrite_request_body(_body(_main_call(LONG)), Config())
    assert not did  # engine failed open -> request forwarded unchanged


def test_rewrite_fuzz_never_raises():
    samples = [
        b"",
        b"{",
        b"[]",
        b"null",
        b"true",
        b"123",
        b'{"messages":[]}',
        b'{"messages":[{"role":"user"}]}',
        b'{"model":"opus","tools":[1],"messages":[{"role":"user","content":[{"type":"x"}]}]}',
        b'{"model":123,"messages":"x"}',
        b"\xff\xfe\x00",
        b'{"messages":{}}',
        b'{"model":"opus","tools":[1],"messages":[{"role":"user","content":[]}]}',
    ]
    for s in samples:
        out, did = proxy.rewrite_request_body(s, Config())
        assert isinstance(out, bytes) and isinstance(did, bool)

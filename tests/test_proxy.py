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


def test_dry_run_forwards_unchanged(monkeypatch):
    monkeypatch.setattr(proxy, "enhance", _no_enhance)
    cfg = Config()
    cfg.proxy_dry_run = True
    new, did = proxy.rewrite_request_body(_body(_main_call(LONG)), cfg)
    assert not did


def test_count_tokens_is_not_a_messages_call():
    assert proxy._is_messages_path("/v1/messages")
    assert proxy._is_messages_path("/v1/messages?beta=true")
    assert not proxy._is_messages_path("/v1/messages/count_tokens")


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


# --- logging + OpenTelemetry (opt-in observability) ------------------------ #


def test_setup_logging_sets_level():
    proxy._setup_logging("debug")
    assert proxy.logger.level == 10  # logging.DEBUG
    proxy.logger.handlers[:] = []  # leave the global logger clean for other tests


def test_otel_disabled_by_default_is_noop():
    # With tracing off (default config, env unset by conftest) the span yields None and
    # _get_tracer never even imports the SDK.
    proxy._otel_tracer[:] = []
    assert proxy._get_tracer(Config()) is None
    with proxy._otel_span("x", Config()) as span:
        assert span is None


def test_otel_enabled_records_span(monkeypatch):
    spans = []

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            spans.append((k, v))

    class _Tracer:
        def start_as_current_span(self, name):
            spans.append(("span", name))
            return _Span()

    proxy._otel_tracer[:] = []
    monkeypatch.setattr(proxy, "_get_tracer", lambda cfg: _Tracer())
    monkeypatch.setattr(proxy, "enhance", lambda text, **k: EnhanceResult("ENHANCED", True, text))
    cfg = Config()
    cfg.otel_enabled = True
    new, did = proxy.rewrite_request_body(_body(_main_call(LONG)), cfg)
    assert did
    assert ("span", "prompt_preflight.enhance") in spans
    assert any(k == "model" for k, _ in spans)
    proxy._otel_tracer[:] = []


def test_concurrent_rewrites_are_safe(monkeypatch):
    """#41: hammer the rewrite path from many threads through a shared semaphore and
    assert every call returns a correct, independent result with no crashes."""
    import threading

    monkeypatch.setattr(proxy, "enhance", lambda text, **k: EnhanceResult(text.upper(), True, text))
    sema = threading.Semaphore(8)
    results: list = []
    errors: list = []
    lock = threading.Lock()

    def worker(i):
        try:
            prompt = f"{LONG} number {i} please and thanks a lot for the help"
            new, did = proxy.rewrite_request_body(
                _body(_main_call(prompt)), Config(), semaphore=sema
            )
            content = json.loads(new)["messages"][-1]["content"]
            with lock:
                results.append((did, content == prompt.upper()))
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 50
    assert all(did and matched for did, matched in results)

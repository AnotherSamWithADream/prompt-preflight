"""Live integration tests -- they call the REAL ``claude`` binary with Haiku.

These are skipped by default. To run them::

    set PROMPT_ENHANCER_LIVE_TESTS=1   (Windows)   /   export ... (POSIX)
    pytest -m live

They consume a small amount of Haiku usage and depend on the model, so the assertions
are deliberately loose -- they check *behaviour* (faithfulness, sensible expansion),
not exact wording.
"""

import os
import shutil

import pytest

from prompt_enhancer.engine import enhance

_live_enabled = os.environ.get("PROMPT_ENHANCER_LIVE_TESTS") == "1" and shutil.which("claude")

requires_live = pytest.mark.skipif(
    not _live_enabled,
    reason="set PROMPT_ENHANCER_LIVE_TESTS=1 and have `claude` on PATH to run live tests",
)


@pytest.mark.live
@requires_live
def test_faithfulness_on_a_clear_prompt():
    raw = (
        "Refactor the function parse_dates in utils.py to use datetime.strptime "
        "and add a docstring."
    )
    result = enhance(raw)
    assert result.enhanced, f"expected enhancement, got fail-open: {result.error}"
    low = result.text.lower()
    # Every concrete identifier must survive verbatim -- the rewriter may not drop or
    # invent specifics.
    for token in ("parse_dates", "utils.py", "datetime.strptime", "docstring"):
        assert token in low, f"faithfulness violation: '{token}' missing from rewrite"


@pytest.mark.live
@requires_live
def test_sensible_expansion_on_a_vague_prompt():
    raw = "make my code faster and also can you add some tests for it"
    result = enhance(raw)
    assert result.enhanced, f"expected enhancement, got fail-open: {result.error}"
    # A vague prompt should gain clarity -- typically clarifying "Open questions" -- and
    # must not collapse to something shorter than the input.
    assert len(result.text) >= len(raw) * 0.7
    gained_questions = "?" in result.text or "open questions" in result.text.lower()
    grew = len(result.text.split()) > len(raw.split())
    assert gained_questions or grew, "expected the vague prompt to be expanded/clarified"
    # And it must not have been answered: a rewrite of a code request should not contain
    # a fabricated code block.
    assert "```" not in result.text


@pytest.mark.live
@requires_live
def test_raw_text_still_rewrites_at_engine_level():
    # The //raw bypass lives in the hook/CLI, not the engine. The engine always tries.
    result = enhance("write a haiku about static analysis and type checking in python")
    assert result.enhanced


@pytest.mark.live
@requires_live
def test_proxy_injects_real_enhancement_end_to_end():
    """The headline path, live: a real main-turn request through the proxy gets its prompt
    rewritten by REAL claude, and the *enhanced* (faithful) text is what reaches upstream.

    Uses a fake upstream so the relay/auth isn't exercised here (that's covered separately) --
    this test is specifically about the proxy + the real engine producing a real rewrite."""
    import http.client
    import http.server
    import json
    import threading

    from prompt_enhancer.config import Config
    from prompt_enhancer.proxy import make_server

    # A real main-turn prompt: >= word_threshold words (so the proxy doesn't pass it
    # through as "too short"), with hard specifics that must survive the rewrite.
    raw = (
        "Please refactor the function parse_dates in utils.py so it uses datetime.strptime, "
        "and also add a clear docstring describing the parameters and the return value"
    )
    # Probe: if claude can't enhance right now (transient/unavailable), there's nothing to
    # assert about proxy injection -- skip rather than fail on external flakiness.
    if not enhance(raw, config=Config()).enhanced:
        pytest.skip("claude transiently unavailable; cannot exercise the live proxy path")

    received: dict = {}

    class _Upstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            received["body"] = self.rfile.read(n)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()

    cfg = Config()
    cfg.upstream_base = f"http://127.0.0.1:{up.server_address[1]}"
    cfg.proxy_port = 0
    server = make_server(cfg)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        payload = {
            "model": "claude-opus-4-8",  # a strong model (not skipped) + tools -> eligible
            "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": raw}]}],
        }
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request(
            "POST",
            "/v1/messages",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        conn.getresponse().read()
        sent = json.loads(received["body"])
        sent_text = sent["messages"][-1]["content"][0]["text"]
        assert sent_text != raw, "proxy forwarded the ORIGINAL -- real enhancement did not run"
        # The real rewrite must keep every hard specific (faithfulness through the proxy).
        for tok in ("parse_dates", "utils.py", "datetime.strptime"):
            assert tok in sent_text, f"faithfulness lost through proxy: {tok!r} missing"
    finally:
        server.shutdown()
        server.server_close()
        up.shutdown()
        up.server_close()

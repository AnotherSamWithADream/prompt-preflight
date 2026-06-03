"""Integration tests for the proxy's HTTP layer: a real fake upstream + real sockets.

Verifies that the enhanced body actually reaches upstream, the upstream response is
relayed back to the client, the health endpoints work, body-size/chunked limits apply,
and bind-safety is enforced. The enhancement engine is mocked (no network/claude)."""

import http.client
import http.server
import json
import threading

import pytest

from prompt_enhancer import proxy
from prompt_enhancer.config import Config
from prompt_enhancer.engine import EnhanceResult

OPUS = "claude-opus-4-8"
TOOLS = [{"name": "Read", "input_schema": {"type": "object"}}]
REMINDER = {"type": "text", "text": "<system-reminder>\nctx\n</system-reminder>"}
LONG = "please optimize this module for performance and add tests across the codebase now"
SSE_BODY = b'event: message_start\ndata: {"type":"message_start"}\n\ndata: [DONE]\n\n'


class _UpstreamHandler(http.server.BaseHTTPRequestHandler):
    last_body = None

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        _UpstreamHandler.last_body = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(SSE_BODY)))
        self.end_headers()
        self.wfile.write(SSE_BODY)

    def log_message(self, *a):
        pass


def _serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()


def _start_proxy(cfg, skip_texts=None):
    cfg.proxy_port = 0
    server = proxy.make_server(cfg, skip_texts=skip_texts)
    _serve(server)
    return server, server.server_address[1]


def test_proxy_relays_enhanced_body_and_streams_response(monkeypatch):
    monkeypatch.setattr(
        proxy, "enhance", lambda text, **k: EnhanceResult("ENHANCED PROMPT", True, text)
    )
    up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    _serve(up)
    cfg = Config()
    cfg.upstream_base = f"http://127.0.0.1:{up.server_address[1]}"
    server, pport = _start_proxy(cfg)
    try:
        payload = {
            "model": OPUS,
            "tools": TOOLS,
            "messages": [{"role": "user", "content": [REMINDER, {"type": "text", "text": LONG}]}],
        }
        conn = http.client.HTTPConnection("127.0.0.1", pport, timeout=10)
        conn.request(
            "POST",
            "/v1/messages?beta=true",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = resp.read()
        # upstream received ONLY the enhanced human block; reminder preserved
        up_payload = json.loads(_UpstreamHandler.last_body)
        blocks = up_payload["messages"][-1]["content"]
        assert blocks[0]["text"].startswith("<system-reminder>")
        assert blocks[1]["text"] == "ENHANCED PROMPT"
        # client got the upstream SSE body relayed back
        assert resp.status == 200
        assert data == SSE_BODY
    finally:
        server.shutdown()
        server.server_close()
        up.shutdown()
        up.server_close()


def test_healthz_and_stats():
    cfg = Config()
    server, pport = _start_proxy(cfg)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", pport, timeout=5)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        assert resp.status == 200 and json.loads(resp.read())["status"] == "ok"
        conn.request("GET", "/stats")
        resp = conn.getresponse()
        assert "requests" in json.loads(resp.read())
        conn.request("GET", "/metrics")
        resp = conn.getresponse()
        assert b"prompt_preflight_requests" in resp.read()
    finally:
        server.shutdown()
        server.server_close()


def test_rejects_oversized_body():
    cfg = Config()
    cfg.proxy_max_body_bytes = 10
    server, pport = _start_proxy(cfg)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", pport, timeout=5)
        conn.request(
            "POST", "/v1/messages", body="x" * 100, headers={"Content-Type": "application/json"}
        )
        assert conn.getresponse().status == 413
    finally:
        server.shutdown()
        server.server_close()


def test_rejects_chunked_request():
    cfg = Config()
    server, pport = _start_proxy(cfg)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", pport, timeout=5)
        conn.putrequest("POST", "/v1/messages")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        conn.send(b"5\r\nhello\r\n0\r\n\r\n")
        assert conn.getresponse().status == 411
    finally:
        server.shutdown()
        server.server_close()


def test_make_server_refuses_public_bind():
    cfg = Config()
    cfg.proxy_host = "0.0.0.0"
    with pytest.raises(ValueError):
        proxy.make_server(cfg)
    cfg.allow_public_bind = True
    server = proxy.make_server(cfg)  # allowed with opt-in
    server.server_close()


def test_banner_mentions_endpoints():
    text = proxy._banner(Config(), "cli")
    assert "/healthz" in text and "ANTHROPIC_BASE_URL" in text


def test_inherit_upstream(monkeypatch):
    cfg = Config()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")
    proxy.inherit_upstream(cfg)
    assert cfg.upstream_base == "https://gateway.example.com"
    # but NOT when it points at our own proxy
    cfg2 = Config()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{cfg2.proxy_port}")
    proxy.inherit_upstream(cfg2)
    assert cfg2.upstream_base == "https://api.anthropic.com"

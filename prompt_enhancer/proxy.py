"""Local enhancing proxy -- true prompt replacement for interactive Claude Code.

Point Claude Code at this proxy with ``ANTHROPIC_BASE_URL=http://HOST:PORT``. For each
``POST /v1/messages`` aimed at your *strong* model, it rewrites the last user message
with the enhancement engine and forwards ONLY the enhanced version upstream. Every other
request (background/title calls, tool-loop turns, non-message endpoints) streams through
untouched.

The response is relayed **raw** (we only change the request body), so streaming/SSE
framing is preserved byte-for-byte. By default we force ``Connection: close`` upstream so
one response == one socket lifetime and there is no response framing to parse (we cache
the TLS context to keep per-request setup cheap). Setting ``proxy_keep_alive`` opts into a
per-thread pooled upstream connection that re-frames responses as it relays them -- a
little faster, at the cost of the connection-reframing code path.

Also serves ``GET /healthz``, ``/readyz``, ``/version``, ``/stats``, and ``/metrics``
(Prometheus) for monitoring.

Usually you don't run this directly -- plain ``enhance`` starts it and launches claude
for you. ``enhance --serve-only`` (or ``python -m prompt_enhancer.proxy``) runs just the
server.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import logging
import os
import signal
import socket
import ssl
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from prompt_enhancer.config import Config, load_config, points_at_proxy
from prompt_enhancer.engine import enhance
from prompt_enhancer.policy import classify_prompt

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

# Built once: constructing an SSLContext loads the CA bundle and is not free.
_SSL_CONTEXT = ssl.create_default_context()

# Per-thread pooled upstream connection, used only when proxy_keep_alive is enabled.
_thread_conn = threading.local()


def _new_upstream_conn(up, cfg: Config):
    host = up.hostname
    port = up.port or (443 if up.scheme == "https" else 80)
    if up.scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=cfg.proxy_upstream_timeout, context=_SSL_CONTEXT
        )
    return http.client.HTTPConnection(host, port, timeout=cfg.proxy_upstream_timeout)


#: Structural logger -- emits metadata only, never prompt text (see :func:`_setup_logging`).
logger = logging.getLogger("prompt_preflight.proxy")


def _setup_logging(level: str | None) -> None:
    """Configure stdlib logging for the proxy. ``level`` is a name like ``info``/``debug``;
    falsy or unrecognised values leave logging at its (silent) default."""
    if not level:
        return
    lvl = getattr(logging, str(level).upper(), None)
    if not isinstance(lvl, int):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(lvl)
    logger.propagate = False


def _debug(msg: str) -> None:
    """Structural diagnostics (never prompt text). Enabled with PROMPT_ENHANCER_PROXY_DEBUG=1
    or by raising the log level to DEBUG via ``--log-level debug``."""
    if os.environ.get("PROMPT_ENHANCER_PROXY_DEBUG"):
        sys.stderr.write(f"[proxy-debug] {msg}\n")
        sys.stderr.flush()
    logger.debug(msg)


def _shape(content) -> str:
    if isinstance(content, str):
        return "str"
    if isinstance(content, list):
        return (
            "list["
            + ",".join(
                (b.get("type", "?") if isinstance(b, dict) else type(b).__name__) for b in content
            )
            + "]"
        )
    return type(content).__name__


# --------------------------------------------------------------------------- #
# Request-body rewriting (pure; unit-tested)                                  #
# --------------------------------------------------------------------------- #


def _extract_user_prompt(message: dict, marker: str = "<system-reminder"):
    """Return ``(text, setter)`` for the human's prompt in a fresh user turn, else
    ``(None, None)``. A fresh human turn is text-only (no tool_result/tool_use/image
    blocks). When Claude Code attaches context as extra text blocks containing ``marker``,
    the human prompt is the single remaining non-marker block."""
    content = message.get("content")
    if isinstance(content, str):

        def setter(new):
            message["content"] = new

        return content, setter

    if isinstance(content, list):
        if any(not (isinstance(b, dict) and b.get("type") == "text") for b in content):
            return None, None
        human = [b for b in content if marker not in (b.get("text", "") or "")]
        if len(human) == 1:
            block = human[0]

            def setter(new):
                block["text"] = new

            return block.get("text", ""), setter
    return None, None


def _dump(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# OpenTelemetry is optional: the cache holds [] until first use, then [tracer|None].
_otel_tracer: list = []


def _get_tracer(cfg: Config):
    """Return an OTel tracer if tracing is opted-in and the SDK is importable, else None."""
    if not (cfg.otel_enabled or os.environ.get("PROMPT_ENHANCER_OTEL")):
        return None
    if _otel_tracer:
        return _otel_tracer[0]
    tracer = None
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("prompt_preflight.proxy")
    except Exception:  # noqa: BLE001 -- tracing must never break the proxy
        tracer = None
    _otel_tracer.append(tracer)
    return tracer


@contextlib.contextmanager
def _otel_span(name: str, cfg: Config, attrs: dict | None = None):
    """A span context manager that is a no-op unless tracing is enabled. Attributes are
    metadata only (model, char counts) -- prompt text is never recorded."""
    tracer = _get_tracer(cfg)
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        try:
            for key, value in (attrs or {}).items():
                span.set_attribute(key, value)
        except Exception:  # noqa: BLE001
            pass
        yield span


def rewrite_request_body(raw: bytes, cfg: Config, skip_texts=None, semaphore=None):
    """Return ``(body_bytes, did_rewrite)``. Fails safe: any uncertainty -> unchanged.

    ``skip_texts``: prompts the launcher already enhanced (forwarded unchanged).
    ``semaphore``: optional concurrency limiter held only around the enhancement call.
    """
    if not raw:
        return raw, False
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        _debug("skip: body is not JSON")
        return raw, False
    if not isinstance(payload, dict) or not cfg.enabled:
        _debug("skip: not a dict / disabled")
        return raw, False

    model = str(payload.get("model", "")).lower()
    if any(skip.lower() in model for skip in cfg.proxy_skip_models):
        _debug(f"skip: model={model!r} matches skip list {list(cfg.proxy_skip_models)}")
        return raw, False

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        _debug("skip: no messages list")
        return raw, False
    last = messages[-1]

    if os.environ.get("PROMPT_ENHANCER_PROXY_DEBUG"):
        lc = last.get("content") if isinstance(last, dict) else None
        _debug(
            f"req: model={model!r} n_msgs={len(messages)} "
            f"last_role={last.get('role') if isinstance(last, dict) else None!r} "
            f"tools={bool(payload.get('tools'))} last_shape={_shape(lc)}"
        )

    if not isinstance(last, dict) or last.get("role") != "user":
        _debug("skip: last message is not a fresh user turn")
        return raw, False

    if cfg.proxy_require_tools and not payload.get("tools"):
        _debug("skip: no tools (background/utility call)")
        return raw, False

    text, setter = _extract_user_prompt(last, cfg.proxy_reminder_marker)
    if text is None:
        _debug(
            f"skip: last user content shape={_shape(last.get('content'))} (no single human text block)"
        )
        return raw, False

    if skip_texts and text.strip() in skip_texts:
        _debug("skip: prompt already enhanced by the launcher (first prompt)")
        return raw, False

    decision = classify_prompt(text, cfg)
    if decision.action == "passthrough":
        _debug(f"skip: classify=passthrough (words={len(text.split())})")
        return raw, False
    if decision.action == "raw":
        setter(decision.text)
        _debug("rewrite: //raw bypass (stripped token)")
        return _dump(payload), True

    if cfg.proxy_dry_run:
        _debug(
            f"dry-run: WOULD enhance (model={model!r}, {len(text)} chars) -- forwarding unchanged"
        )
        return raw, False

    with _otel_span("prompt_preflight.enhance", cfg, {"model": model, "input_chars": len(text)}):
        if semaphore is not None:
            with semaphore:
                result = enhance(decision.text, config=cfg)
        else:
            result = enhance(decision.text, config=cfg)
    if not result.enhanced:
        _debug(f"skip: engine fail-open ({result.error})")
        return raw, False
    setter(result.text)
    _debug(
        f"rewrite: enhanced via {result.backend} ({len(text)}->{len(result.text)} chars, model={model!r})"
    )
    return _dump(payload), True


def _is_messages_path(path: str) -> bool:
    return path.split("?", 1)[0].rstrip("/").endswith("/v1/messages")


# --------------------------------------------------------------------------- #
# Stats + access log                                                          #
# --------------------------------------------------------------------------- #


class _Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._latency: deque = deque(maxlen=512)  # recent request ms (bounded)
        self.requests = 0
        self.rewrites = 0
        self.fail_opens = 0
        self.upstream_errors = 0

    def record(self, *, rewrote: bool, ms: float | None = None):
        with self._lock:
            self.requests += 1
            if rewrote:
                self.rewrites += 1
            if ms is not None:
                self._latency.append(ms)

    def note_upstream_error(self):
        with self._lock:
            self.upstream_errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            lat = sorted(self._latency)
            return {
                "requests": self.requests,
                "rewrites": self.rewrites,
                "fail_opens": self.fail_opens,
                "upstream_errors": self.upstream_errors,
                "uptime_s": round(time.monotonic() - self._start, 1),
                "p50_ms": _percentile(lat, 50),
                "p95_ms": _percentile(lat, 95),
            }


def _percentile(sorted_vals, pct: int):
    if not sorted_vals:
        return 0
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100) * (len(sorted_vals) - 1)))))
    return round(sorted_vals[k])


def _access_log(record: dict) -> None:
    """Opt-in, local-only access log (metadata only -- never prompt text)."""
    logger.info("access %s", json.dumps(record))
    path = os.environ.get("PROMPT_ENHANCER_PROXY_ACCESS_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# HTTP handler (transparent relay)                                            #
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60  # drop hung/slowloris client connections
    _cfg: Config = None  # type: ignore[assignment]  # bound per-server via make_server
    _skip_texts = None  # prompts the launcher already enhanced
    _sema = None  # enhancement concurrency limiter
    _stats = None  # _Stats

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _relay(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if self.command == "GET" and path in ("/healthz", "/readyz"):
            return self._respond_json(200, {"status": "ok"})
        if self.command == "GET" and path == "/stats":
            return self._respond_json(200, self._stats.snapshot() if self._stats else {})
        if self.command == "GET" and path == "/metrics":
            snap = self._stats.snapshot() if self._stats else {}
            body = "".join(
                f"# TYPE prompt_preflight_{k} gauge\nprompt_preflight_{k} {v}\n"
                for k, v in snap.items()
            )
            return self._respond_text(200, body)
        if self.command == "GET" and path == "/version":
            from prompt_enhancer import __version__

            return self._respond_json(200, {"version": __version__})

        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._safe_error(411, "chunked request bodies are not supported")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > self._cfg.proxy_max_body_bytes:
            return self._safe_error(413, "request body too large")
        body = self.rfile.read(length) if length > 0 else b""

        t0 = time.monotonic()
        did = False
        if self.command == "POST" and _is_messages_path(self.path):
            try:
                body, did = rewrite_request_body(
                    body, self._cfg, skip_texts=self._skip_texts, semaphore=self._sema
                )
            except Exception:  # noqa: BLE001 -- never let rewriting break the request
                pass

        try:
            self._forward(body)
            ms = (time.monotonic() - t0) * 1000
            if self._stats:
                self._stats.record(rewrote=did, ms=ms)
            _access_log(
                {
                    "method": self.command,
                    "path": path,
                    "rewrote": did,
                    "ms": round(ms),
                    "request_id": self.headers.get("x-request-id"),
                }
            )
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
        except OSError as exc:
            if self._stats:
                self._stats.record(rewrote=did)
                self._stats.note_upstream_error()
            self._safe_error(502, f"upstream error: {type(exc).__name__}: {exc}")

    do_POST = _relay
    do_GET = _relay
    do_PUT = _relay
    do_PATCH = _relay
    do_DELETE = _relay
    do_OPTIONS = _relay

    def _fwd_headers(self):
        """Headers to forward upstream (drop hop-by-hop/Host/Content-Length and any with CR/LF)."""
        out = {}
        for key, value in self.headers.items():
            low = key.lower()
            if low in _HOP_BY_HOP or low in ("host", "content-length"):
                continue
            if any(c in key or c in str(value) for c in ("\r", "\n")):
                continue  # defend against header / request smuggling
            out[key] = value
        return out

    def _forward(self, body: bytes):
        if self._cfg.proxy_keep_alive:
            return self._forward_keepalive(body)
        return self._forward_close(body)

    def _forward_close(self, body: bytes):
        """Raw relay: one response per socket (Connection: close), framing preserved verbatim."""
        up = urlsplit(self._cfg.upstream_base)
        host = up.hostname
        port = up.port or (443 if up.scheme == "https" else 80)
        raw = socket.create_connection((host, port), timeout=self._cfg.proxy_connect_timeout)
        raw.settimeout(self._cfg.proxy_upstream_timeout)
        sock = _SSL_CONTEXT.wrap_socket(raw, server_hostname=host) if up.scheme == "https" else raw

        try:
            lines = [f"{self.command} {self.path} HTTP/1.1"]
            for key, value in self._fwd_headers().items():
                lines.append(f"{key}: {value}")
            lines.append(f"Host: {host}")
            lines.append(f"Content-Length: {len(body)}")
            lines.append("Connection: close")
            head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
            sock.sendall(head + body)

            self.close_connection = True
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                self.wfile.write(data)
                self.wfile.flush()
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _forward_keepalive(self, body: bytes):
        """Pooled upstream connection (reused per thread). Re-frames the response as chunked."""
        up = urlsplit(self._cfg.upstream_base)
        key = (up.scheme, up.hostname, up.port)
        conn = getattr(_thread_conn, "conn", None)
        if conn is None or getattr(_thread_conn, "key", None) != key:
            conn = _new_upstream_conn(up, self._cfg)
            _thread_conn.conn, _thread_conn.key = conn, key
        headers = self._fwd_headers()
        headers["Host"] = up.hostname
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except (http.client.HTTPException, OSError):
            try:
                conn.close()
            except OSError:
                pass
            conn = _new_upstream_conn(up, self._cfg)
            _thread_conn.conn = conn
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()

        self.send_response_only(resp.status, resp.reason or "")
        for k, v in resp.getheaders():
            if k.lower() in ("connection", "keep-alive", "transfer-encoding", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while True:
            chunk = resp.read(65536)
            self._write_chunk(chunk)
            if not chunk:
                break  # terminator written

    def _write_chunk(self, data: bytes):
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _respond_json(self, code: int, obj: dict):
        payload = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, ValueError):
            pass
        self.close_connection = True

    def _respond_text(self, code: int, text: str):
        payload = text.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, ValueError):
            pass
        self.close_connection = True

    def _safe_error(self, code: int, message: str):
        self._respond_json(code, {"error": message})

    def log_message(self, *args):
        pass  # privacy: never log request lines


# --------------------------------------------------------------------------- #
# Server construction                                                          #
# --------------------------------------------------------------------------- #


def _is_loopback(host: str) -> bool:
    h = (host or "").lower()
    return h in ("127.0.0.1", "::1", "localhost") or h.startswith("127.")


def make_server(cfg: Config, skip_texts=None) -> ThreadingHTTPServer:
    if not cfg.allow_public_bind and not _is_loopback(cfg.proxy_host):
        raise ValueError(
            f"refusing to bind the proxy to non-loopback host {cfg.proxy_host!r} "
            "(it is unauthenticated). Set allow_public_bind=true / "
            "PROMPT_ENHANCER_ALLOW_PUBLIC_BIND=1 to override."
        )
    handler = type(
        "BoundHandler",
        (_Handler,),
        {
            "_cfg": cfg,
            "_skip_texts": skip_texts or set(),
            "_sema": threading.Semaphore(max(1, cfg.proxy_max_concurrency)),
            "_stats": _Stats(),
        },
    )
    server = ThreadingHTTPServer((cfg.proxy_host, cfg.proxy_port), handler)
    server.daemon_threads = True
    return server


def inherit_upstream(cfg: Config) -> Config:
    """If ANTHROPIC_BASE_URL points somewhere other than our own proxy (e.g. a corporate
    LLM gateway), forward to it instead of the default so gateway users aren't bypassed."""
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if base and not points_at_proxy(base, cfg):
        cfg.upstream_base = base
    return cfg


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #


def _banner(cfg: Config, backend: str) -> str:
    base = f"http://{cfg.proxy_host}:{cfg.proxy_port}"
    return (
        f"prompt-preflight proxy (serve-only) listening on {base}\n"
        "  Tip: plain `enhance` starts this AND launches claude for you.\n"
        f"  backend       : {backend}\n"
        f"  upstream      : {cfg.upstream_base}\n"
        f"  enhances when : model NOT in {list(cfg.proxy_skip_models)} and prompt >= {cfg.word_threshold} words\n"
        f"  endpoints     : {base}/healthz  {base}/stats\n\n"
        "Point a separate Claude Code session at it (that terminal only):\n"
        f"  PowerShell : $env:ANTHROPIC_BASE_URL = '{base}'; claude\n"
        f"  bash/zsh   : ANTHROPIC_BASE_URL={base} claude\n\n"
        "Your strong model will see only the enhanced prompt. Ctrl+C to stop.\n"
    )


def main(argv=None) -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(
        prog="enhance --serve-only",
        description="Local proxy that rewrites your prompt before your strong model sees it.",
    )
    parser.add_argument("--host", default=cfg.proxy_host)
    parser.add_argument("--port", type=int, default=cfg.proxy_port)
    parser.add_argument("--upstream", default=None)
    parser.add_argument(
        "--log-level",
        default=os.environ.get("PROMPT_ENHANCER_LOG_LEVEL"),
        help="Structural logging level (debug/info/warning); metadata only, never prompt text.",
    )
    args = parser.parse_args(argv)

    _setup_logging(args.log_level)
    cfg.proxy_host, cfg.proxy_port = args.host, args.port
    if args.upstream:
        cfg.upstream_base = args.upstream
    else:
        inherit_upstream(cfg)

    backend = (
        "api"
        if (cfg.backend == "api" or (cfg.backend == "auto" and os.environ.get(cfg.api_key_env)))
        else "cli"
    )

    try:
        server = make_server(cfg)
    except ValueError as exc:
        sys.stderr.write(f"enhance: {exc}\n")
        return 2
    except OSError as exc:
        sys.stderr.write(
            f"enhance: cannot bind proxy to {cfg.proxy_host}:{cfg.proxy_port} ({exc})\n"
        )
        return 1

    def _stop(*_a):
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass  # not in main thread (e.g. tests) -> rely on KeyboardInterrupt

    sys.stderr.write(_banner(cfg, backend))
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nstopping...\n")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Proxy

The proxy is the only path that does **true prompt replacement** for interactive Claude
Code. Point Claude Code at it with `ANTHROPIC_BASE_URL=http://HOST:PORT` and, for each
`POST /v1/messages` aimed at your *strong* model, it rewrites the last user message and
forwards **only** the enhanced version upstream.

Usually you don't run it directly — `enhance` starts it and launches `claude` for you:

```bash
enhance "your first prompt"      # rewrite + proxy + interactive claude
enhance --serve-only             # just the server
python -m prompt_enhancer.proxy  # equivalent
```

## What gets rewritten — and what doesn't

The proxy is conservative by design. It rewrites only a genuine *main* user turn and
streams everything else through untouched:

- **Skips** background/title calls (no `tools`), tool-loop turns (ending in a
  `tool_result`), non-message endpoints (`/v1/messages/count_tokens`, model list), and any
  model on the skip list (e.g. Haiku background calls).
- **Skips** the launcher's already-enhanced first prompt (so it isn't enhanced twice).
- **Honors** `//raw` (strips the token, forwards as-is) and the word-count threshold.
- **Fails open** — any uncertainty forwards the request unchanged.

## Response relay

Responses are relayed **raw** — only the request body is changed — so streaming/SSE
framing is preserved byte-for-byte. By default the proxy forces `Connection: close`
upstream (one response = one socket; no framing to parse) and caches the TLS context to
keep setup cheap. Setting `proxy_keep_alive` opts into a per-thread pooled upstream
connection that re-frames responses as it relays them.

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /healthz` | liveness |
| `GET /readyz` | readiness |
| `GET /version` | version string |
| `GET /stats` | JSON: requests, rewrites, uptime, p50/p95 latency, per-decision counts |
| `GET /metrics` | Prometheus exposition |

`enhance-cli stats` pretty-prints `/stats` from a running proxy.

## Dry-run

`proxy_dry_run` (or `PROMPT_ENHANCER_PROXY_DRY_RUN=1`) logs what *would* be rewritten —
the decision and before/after sizes — without changing any request. Use it to measure
traffic and build trust before enabling rewriting.

## Safety on bind

The proxy refuses to bind a non-loopback host unless you explicitly set
`allow_public_bind` / `PROMPT_ENHANCER_ALLOW_PUBLIC_BIND=1`.

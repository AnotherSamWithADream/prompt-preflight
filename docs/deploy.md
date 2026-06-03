# Deploy & operate

For most users prompt-preflight runs locally and needs no deployment. This page is for
running the proxy as a shared service.

## Docker

A published image is available on GHCR (built and pushed by CI):

```bash
docker run --rm -p 8788:8788 \
  -e ANTHROPIC_API_KEY=sk-... \
  ghcr.io/anothersamwithadream/prompt-preflight --serve-only --host 0.0.0.0
```

Binding a non-loopback host requires `--host 0.0.0.0` **and**
`PROMPT_ENHANCER_ALLOW_PUBLIC_BIND=1`.

## Logging

```bash
enhance --serve-only --log-level info
```

Structural logging is **metadata only** — never prompt text. `--log-level debug` adds
per-request decisions (sizes, models, decision codes). An opt-in JSONL access log is
written when `PROMPT_ENHANCER_PROXY_ACCESS_LOG=<path>` is set.

## Metrics

`GET /metrics` exposes Prometheus gauges (requests, rewrites, upstream errors, p50/p95
latency, uptime). A ready-made Grafana dashboard lives at
[`deploy/grafana-dashboard.json`](https://github.com/AnotherSamWithADream/prompt-preflight/blob/main/deploy/grafana-dashboard.json).

## Tracing (opt-in)

Set `otel_enabled` / `PROMPT_ENHANCER_OTEL=1` (with the OpenTelemetry SDK installed) to
emit a span around each enhancement. Span attributes are metadata only (model, char
counts). If the SDK isn't installed or tracing is off, it's a no-op.

## Install channels

| Channel | Command |
|---------|---------|
| pip | `pip install prompt-preflight` |
| uv (one-shot) | `uvx prompt-preflight` |
| pipx (one-shot) | `pipx run prompt-preflight` |
| Homebrew | `brew install --formula packaging/homebrew/prompt-preflight.rb` |
| Scoop | `scoop install packaging/scoop/prompt-preflight.json` |

## Shell widgets

Bind a key to rewrite the current command-line buffer in place:

- bash/zsh: `source packaging/shell/enhance-widgets.sh` → `Ctrl-X Ctrl-E`
- PowerShell: `. packaging/shell/enhance-widget.ps1` → `Ctrl+x,Ctrl+e`

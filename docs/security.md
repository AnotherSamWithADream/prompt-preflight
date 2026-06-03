# Security & privacy

Privacy and safety are first-class design constraints, not afterthoughts.

## Nothing on disk by default

Prompt contents are never logged or written to disk unless you explicitly opt in:

- `PROMPT_ENHANCER_LOG=<path>` — local-only diagnostics, **metadata only** (timings,
  sizes, fail-open reasons).
- `PROMPT_ENHANCER_LOG_CONTENT=1` — *additionally* records prompt text (off by default).
- Proxy debug (`PROMPT_ENHANCER_PROXY_DEBUG=1`) and `--log-level` log only request
  *structure*, never prompt text.

The CLI's Edit action writes to a temp file so your editor can open it, then deletes it
immediately.

## Credentials never leave

Before enhancement, the engine scans for secrets (API keys, `Bearer …`, AWS keys, etc.).
If one is found, the prompt is **not** sent to the enhancer — your original (with the
credential you intended) still proceeds to the strong model. Optional PII awareness warns
when a prompt looks like it contains emails/SSNs.

## Fail-open, always

Every failure mode — timeout, bad output, dropped tokens, implausible length, a crashing
plugin, a tripped circuit breaker — returns your **original** text. The worst case is a
small delay, never a blocked or mangled prompt.

## No shell, no argv leak

The `cli` backend passes the prompt on **stdin** and invokes `claude` as an argv list
(no shell), so prompts can't leak into process listings and there's no command injection
surface.

## Switches you control

| Switch | Effect |
|--------|--------|
| `PROMPT_ENHANCER_DISABLE=1` | Global kill-switch honored by engine, hook, proxy, launcher |
| `//raw …` | Bypass enhancement for a single prompt |
| `PROMPT_ENHANCER_ACTIVE=1` | Recursion guard the engine sets for its own child `claude` |
| `allow_public_bind` | Required before the proxy will bind a non-loopback host |

## Hardening in the rewrite path

The proxy treats `<system-reminder>` blocks as injected context, not the human prompt, so
it rewrites only genuine user text. Header relay strips hop-by-hop headers and defends
against CR/LF injection.

## Reporting

Found a vulnerability? See
[`SECURITY.md`](https://github.com/AnotherSamWithADream/prompt-preflight/blob/main/SECURITY.md)
for private disclosure instructions.

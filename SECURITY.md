# Security Policy

## Supported versions

The latest released version receives security fixes. Pre-`1.0` releases may introduce
breaking changes between minor versions.

## Threat model & data flow

prompt-preflight is a local developer tool. It does **not** run a public service by
default, and it has **no telemetry**.

- **What happens to your prompt.** Your prompt is sent to Anthropic's Haiku model to be
  rewritten — either through your local `claude` CLI (reusing the auth you already
  configured for Claude Code) or through the Anthropic API (`ANTHROPIC_API_KEY`). The
  rewritten prompt then goes to your chosen stronger model. Nothing else leaves your
  machine.
- **Credentials.** The tool never reads, stores, or transmits your credentials itself.
  The CLI backend shells out to `claude`, which manages its own auth. The proxy passes
  your `Authorization` / `x-api-key` headers through to Anthropic unchanged and never
  logs them.
- **On disk.** Nothing is written to disk by default. Opt-in diagnostics
  (`PROMPT_ENHANCER_LOG`) record **metadata only** unless `PROMPT_ENHANCER_LOG_CONTENT=1`
  is also set. The proxy's debug output (`PROMPT_ENHANCER_PROXY_DEBUG=1`) logs request
  *structure* only, never prompt text.
- **No shell injection.** `claude` is invoked with an argument list (never a shell
  string) and the prompt is passed on stdin, so prompt contents cannot break out of or
  inject into the command, and do not appear in the process argument list.
- **The proxy** binds to loopback (`127.0.0.1`) by default and is **unauthenticated** —
  do not bind it to a non-loopback interface (the launcher and `--serve-only` refuse to
  unless you explicitly opt in). Any local process can route through it.

## Reporting a vulnerability

Please report security issues **privately** via GitHub Security Advisories ("Report a
vulnerability" on the repository's *Security* tab), or by emailing the maintainer. Do not
open a public issue for security reports. We aim to acknowledge within 7 days and to
coordinate a fix and disclosure timeline with you.

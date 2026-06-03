# prompt-preflight — improvements & roadmap

Status of the v0.2.0 roadmap. Most items below are **delivered**; a short list of
genuinely deferred ideas is tracked at the bottom.

**Priority:** 🟠 high · 🟡 medium · ⚪ nice-to-have

## Faithfulness & output quality (the core promise)

- [x] 🟠 **Programmatic faithfulness check** — verify the original's key tokens (file paths, identifiers, numbers, quoted strings, URLs) survive in the rewrite; **fail open if any are dropped**. Enforces the #1 principle in code, not just the prompt.
- [x] 🟡 **Length-ratio sanity guard** — fail open if the rewrite is wildly shorter/longer than the input.
- [x] 🟡 **Defensive output cleanup** — strip stray code fences, "Here is the rewritten prompt:", or wrapping quotes if the model adds them despite the system prompt.
- [x] 🟡 **Rewrite "profiles"** — `--profile concise|detailed|coding|research`, each a tuned system-prompt variant.
- [x] ⚪ **Optional clarifying-question flow** — when the rewrite contains "Open questions", the CLI prompts you to answer them and folds the answers in before copying.
- [ ] ⚪ **Shrink the system prompt** — *(deferred)* the per-profile suffixes are in; a tighter base prompt is future tuning work.

## New backends & offline

- [x] 🟠 **Local/Ollama backend** — fully offline, zero-cost enhancement for confidential/air-gapped use.
- [x] 🟡 **OpenAI / other-provider backend** — broaden beyond Anthropic.
- [x] 🟡 **Entry-point plugin backends** — third parties register a backend via `prompt_preflight.backends` entry points instead of editing the engine.
- [x] 🟡 **Bedrock/Vertex** support in the api backend (`api_provider`).
- [x] ⚪ **Heuristic no-LLM fallback** — rule-based cleanup when no model/auth is available, so something still happens.

## Privacy & safety (deeper)

- [x] 🟠 **Secret redaction before sending** — detect API keys, `Bearer …`, AWS keys, etc. and never send them to the enhancer (the original still proceeds).
- [x] 🟡 **Result memoization** — opt-in cache (prompt hash → rewrite) to skip re-enhancing retries/repeats.
- [x] 🟡 **Circuit breaker** — after N consecutive fail-opens, pause backend calls for a cooldown.
- [x] 🟡 **Unified kill-switch** — one `PROMPT_ENHANCER_DISABLE=1` honored everywhere.
- [x] ⚪ **PII awareness** — optional warning when a prompt looks like it contains PII.

## Performance

- [x] 🟠 **`--bare` mode for the cli backend** — skips hook/skill/MCP/memory discovery: faster *and* a second layer of recursion defense.
- [x] 🟡 **Real upstream keep-alive/pooling** in the proxy — opt-in via `proxy_keep_alive`.
- [x] 🟠 **Surface token usage & cost** — exposed in `EnhanceResult`, `--json`, and `/stats`.
- [ ] ⚪ **Async engine + async proxy** — *(partial)* `aenhance()` async API shipped; the proxy stays threaded (fine for localhost concurrency). A full asyncio rewrite is deferred to avoid risking the verified raw relay.

## Proxy / architecture

- [x] 🟡 **Proxy dry-run mode** — log what *would* be rewritten without changing requests.
- [x] 🟡 **`/version` + richer `/stats`** — uptime, p50/p95 latency, per-decision counts (+ `/metrics`).
- [x] 🟡 **Real `logging` module** with levels/`--log-level`, replacing ad-hoc `stderr.write`.
- [x] ⚪ **Explicit handling + tests** for other endpoints (`/v1/messages/count_tokens`, model list).
- [x] ⚪ **OpenTelemetry spans** (enhance) behind an opt-in flag.

## CLI / launcher / hook UX

- [x] 🟡 **`enhance init`** — auto-write the hook into `~/.claude/settings.json` (with backup).
- [x] 🟡 **`--explain`** — print the decision trace.
- [x] 🟡 **Read prompt from a file** (`enhance-cli -f prompt.txt`).
- [x] 🟡 **`config unset <key>` / `config reset`.**
- [x] 🟡 **Interactive REPL** (`enhance-cli --repl`).
- [x] ⚪ **Shell widgets** — zsh/bash/PowerShell keybinding that enhances the command-line buffer in place.
- [x] ⚪ **Hook output-style choice** — `hook_output_style: context|minimal`.
- [x] ⚪ **Clipboard-watch mode** — `enhance-cli --watch`.

## Observability & ops

- [x] 🟡 **`enhance-cli stats`** — pretty-print the running proxy's `/stats`.
- [x] ⚪ **Grafana dashboard JSON** for the `/metrics` (`deploy/grafana-dashboard.json`).
- [x] ⚪ **Request-ID correlation** across access log + debug.
- [x] ⚪ **`--no-enhance` proxy mode** — `proxy_dry_run` measures traffic before enabling rewriting.

## Testing & drift-detection

- [x] 🟠 **Scheduled (cron) live-tests CI** — weekly real-model run to catch CLI/model drift.
- [x] 🟠 **System-prompt eval harness** — `scripts/eval_prompts.py` asserts token/length/secret invariants on demand and in CI (heuristic backend).
- [x] 🟡 **Doc-schema contract test** — `tests/test_docs.py` keeps README + config schema in lockstep.
- [x] 🟡 **Proxy load/concurrency test** — concurrent rewrite hammer test.
- [x] ⚪ **`mypy` in CI + raise the coverage gate** — gate raised 70 → 75 (toward 85 as paths get covered).

## Distribution & release

- [x] 🟠 **Cut the first release** — `v0.2.0` tag + GitHub Release shipped; CI green across the matrix. PyPI auto-publish is wired (`publish.yml`, OIDC) and fires on release — it needs a one-time **PyPI trusted-publisher** ("pending publisher") to be configured for `prompt-preflight`, which only the project owner can do.
- [x] 🟡 **Bump Actions to the Node-24 versions** (`checkout@v5`, `setup-python@v6`, …).
- [x] 🟡 **Homebrew tap + Scoop manifest** (`packaging/`).
- [ ] ⚪ **conda-forge recipe** — *(deferred)* `uvx` / `pipx run` one-shot usage is documented; a conda-forge submission is external follow-up.
- [x] ⚪ **Publish the Docker image to GHCR** via a CI build-and-push job.

## Docs & community

- [x] 🟡 **mkdocs-material site on GitHub Pages** — Quickstart / Backends / Proxy / Deploy / Security / Architecture.
- [x] ⚪ **`CITATION.cff`** *(GIF/asciinema demo deferred — needs a recording)*.
- [x] ⚪ **Architecture diagram** and a short comparison vs other prompt-enhancer tools.

---

## Deferred (intentionally not in v0.2.0)

- **Shrink the base system prompt** — tuning work; profiles already cut per-call cost.
- **Full asyncio engine + proxy** — `aenhance()` ships; a full rewrite risks the verified
  byte-for-byte relay for little gain on a local, low-rate proxy.
- **conda-forge recipe** and an **asciinema/GIF demo** — external/manual follow-ups.

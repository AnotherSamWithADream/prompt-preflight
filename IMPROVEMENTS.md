# prompt-preflight — improvements & roadmap

A backlog of proposed improvements, additions, and changes beyond the current release
(v0.2.0). These are forward-looking ideas, not commitments.

**Priority:** 🟠 high · 🟡 medium · ⚪ nice-to-have

## Faithfulness & output quality (the core promise)

- [ ] 🟠 **Programmatic faithfulness check** — verify the original's key tokens (file paths, identifiers, numbers, quoted strings, URLs) survive in the rewrite; **fail open if any are dropped**. Enforces the #1 principle in code, not just the prompt.
- [ ] 🟡 **Length-ratio sanity guard** — fail open if the rewrite is wildly shorter/longer than the input (e.g. <0.4× or >6×); that usually means the model went off the rails.
- [ ] 🟡 **Defensive output cleanup** — strip stray code fences, "Here is the rewritten prompt:", or wrapping quotes if the model adds them despite the system prompt.
- [ ] 🟡 **Rewrite "profiles"** — `--profile concise|detailed|coding|research`, each a tuned system-prompt variant.
- [ ] ⚪ **Optional clarifying-question flow** — when the rewrite contains "Open questions", let the CLI prompt you to answer them and fold the answers in before copying.
- [ ] ⚪ **Shrink the system prompt** — it's ~600 tokens per call; a tighter (or per-profile) version cuts cost even with caching.

## New backends & offline

- [ ] 🟠 **Local/Ollama backend** — fully offline, zero-cost enhancement for confidential/air-gapped use.
- [ ] 🟡 **OpenAI / other-provider backend** — broaden beyond Anthropic (the engine is already backend-abstracted).
- [ ] 🟡 **Entry-point plugin backends** — let third parties register a backend via `[project.entry-points]` instead of editing the engine.
- [ ] 🟡 **Bedrock/Vertex** support in the api backend for enterprises on those providers.
- [ ] ⚪ **Heuristic no-LLM fallback** — rule-based cleanup (whitespace/casing/structure) when no model/auth is available, so something still happens.

## Privacy & safety (deeper)

- [ ] 🟠 **Secret redaction before sending** — detect/strip API keys, `Bearer …`, AWS keys, etc. from the prompt before enhancement (and warn), so credentials never leave even via the rewrite.
- [ ] 🟡 **Result memoization** — opt-in cache (prompt hash → rewrite) to skip re-enhancing retries/repeats.
- [ ] 🟡 **Circuit breaker** — after N consecutive fail-opens, pause backend calls for a cooldown (don't hammer a broken `claude`/API or burn quota).
- [ ] 🟡 **Unified kill-switch** — one `PROMPT_ENHANCER_DISABLE=1` honored everywhere (engine/hook/proxy/launcher).
- [ ] ⚪ **PII awareness** — optional warning when a prompt looks like it contains PII (emails/SSNs).

## Performance

- [ ] 🟠 **`--bare` mode for the cli backend** — `claude -p --bare` skips hook/skill/MCP/memory discovery: ~1s faster *and* a second layer of recursion defense.
- [ ] 🟡 **Real upstream keep-alive/pooling** in the proxy (currently `Connection: close` per request) — cut the per-request TLS handshake.
- [ ] 🟠 **Surface token usage & cost** — the `--output-format json` envelope already returns `usage`/`total_cost_usd`; expose it in `EnhanceResult`, `--json`, and `/stats`.
- [ ] ⚪ **Async engine + async proxy** — replace thread-per-connection with an `asyncio`/`httpx` loop for much higher concurrency.

## Proxy / architecture

- [ ] 🟡 **Proxy dry-run mode** — log what *would* be rewritten (before→after, decision) without changing requests; good for trust-building/debugging.
- [ ] 🟡 **`/version` + richer `/stats`** — uptime, p50/p95 latency, per-decision counts.
- [ ] 🟡 **Real `logging` module** with levels/`--log-level`, replacing ad-hoc `stderr.write`.
- [ ] ⚪ **Explicit handling + tests** for other endpoints (`/v1/messages/count_tokens`, model list) so a future change can't silently break passthrough.
- [ ] ⚪ **OpenTelemetry spans** (enhance, forward) behind an opt-in flag for traced deployments.

## CLI / launcher / hook UX

- [ ] 🟡 **`enhance init`** — auto-write the hook into `~/.claude/settings.json` (with backup) instead of manual copy-paste.
- [ ] 🟡 **`--explain`** — print the decision trace (model, tools, length, `//raw`, why enhanced or not).
- [ ] 🟡 **Read prompt from a file** (`enhance-cli -f prompt.txt`, `enhance -f`).
- [ ] 🟡 **`config unset <key>` / `config reset`.**
- [ ] 🟡 **Interactive REPL** (`enhance-cli --repl`) — paste → rewrite → accept/copy in a loop.
- [ ] ⚪ **Shell widgets** — zsh/bash/PowerShell keybinding that enhances the current command-line buffer in place.
- [ ] ⚪ **Hook output-style choice** — config to pick the labelled `additionalContext` block vs a one-line restatement.
- [ ] ⚪ **Clipboard-watch mode** — `enhance-cli --watch` enhances whatever you copy.

## Observability & ops

- [ ] 🟡 **`enhance-cli stats`** — pretty-print the running proxy's `/stats`.
- [ ] ⚪ **Grafana dashboard JSON** for the `/metrics`.
- [ ] ⚪ **Request-ID correlation** across access log + debug for deployed proxies.
- [ ] ⚪ **`--no-enhance` proxy mode** — measure traffic before enabling rewriting.

## Testing & drift-detection

- [ ] 🟠 **Scheduled (cron) live-tests CI** — run the real-model tests weekly to catch Claude Code CLI/model drift before users do.
- [ ] 🟠 **System-prompt eval harness** — a corpus asserting properties (tokens preserved, no conversational reply, Open-questions only when ambiguous), run on demand.
- [ ] 🟡 **Doc-schema contract test** — fetch the live hooks/CLI docs and flag drift (e.g. if `updatedPrompt` ever ships, or a flag is renamed).
- [ ] 🟡 **Proxy load/concurrency test** + coverage for `proxy.main()`/signal paths.
- [ ] ⚪ **`mypy tests` + raise the coverage gate** toward 85% as paths get covered.

## Distribution & release

- [ ] 🟠 **Cut the first release** — tag `v0.2.0`, create a GitHub Release, and publish to PyPI via `publish.yml` (set up the trusted publisher).
- [ ] 🟡 **Bump Actions to the Node-24 versions** (`checkout@v5`, `setup-python@v6`, …) — clears the deprecation warnings; Dependabot's PRs are already open.
- [ ] 🟡 **Homebrew tap + Scoop manifest** for `brew install` / `scoop install`.
- [ ] ⚪ **conda-forge recipe** and document `uvx prompt-preflight` / `pipx run` one-shot usage.
- [ ] ⚪ **Publish the Docker image to GHCR** via a CI build-and-push job.

## Docs & community

- [ ] 🟡 **mkdocs-material site on GitHub Pages** — split the large README into Quickstart / Backends / Proxy / Deploy / Security.
- [ ] ⚪ **asciinema/GIF demo** in the README + a `CITATION.cff`.
- [ ] ⚪ **Architecture diagram** and a short comparison vs the other prompt-enhancer tools.

---

### Where to start

The highest-value first batch: **cut the release** + **bump the Node-24 Actions** to ship and
clean up CI; the **faithfulness check** + **secret redaction** to harden the core promise and
privacy; the **offline backend**; and **`--bare` mode** + **token/cost reporting** for free
speed and visibility.

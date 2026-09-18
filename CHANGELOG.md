# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-09-18

### Fixed
- **Faithfulness guard rejected faithful rewrites that reformat a number.** A model that
  renders `10000 rps` as `10,000 RPS` has preserved the number exactly, but the
  exact-substring check failed it, so the whole rewrite was discarded. Numeric tokens are
  now matched against a separator-stripped copy of the rewrite; a number that is genuinely
  dropped is still caught. Found by A/B-testing Haiku against Sonnet -- **both** models
  failed the identical prompt, which is what exposed it as a guard bug rather than a model
  limitation.

## [0.3.0] - 2026-09-18

### Added
- **Usage ledger + observability.** A metadata-only, local-only ledger records one line per
  decision -- never prompt text. New: `enhance-cli stats --local`, `enhance-cli digest
  [--days N]`, and `enhance-cli statusline` (for Claude Code's `statusLine`). This answers
  "is it working, how often does it fail open, what is it costing" from the tool itself,
  instead of mining Claude Code transcripts.
- **Spend cap.** `monthly_budget_usd` stops *enhancement* (never the prompt) once
  month-to-date spend reaches the cap.
- **Fail-open visibility.** `show_skips` makes the hook say why it skipped instead of
  emitting nothing -- the silent mode that hid a `--bare` auth regression for months.
- **14 more profiles (19 total):** debugging, review, refactor, testing, architecture,
  performance, security, data, devops, docs, writing, brainstorm, explain, planning.
- **`profile = "auto"`** selects a profile per prompt from its content, falling back to
  `coding` inside a git repository.
- **Conversation-aware rewriting.** The proxy passes the previous `conversation_turns`
  turns as read-only context, so "now do the same for the other one" can resolve to
  something concrete.
- **Repo-aware rewriting.** `repo_context` supplies lightweight, non-sensitive project
  facts (stack, whether it is a git repo) so rewrites use the project's real vocabulary.
  No paths, repo names or file contents are included.
- **Skip already-good prompts.** `skip_well_formed` avoids paying latency and cost to
  rewrite a prompt that is already long, structured and specific.
- **Prompt-injection guard.** `injection_guard` fails open when a rewrite introduces
  instruction-override text or a domain the user never wrote -- the rewrite is fed
  straight into a stronger, agentic model.
- **Structured (JSON) output**, opt-in via `structured_output`. **Measured before
  shipping:** Haiku emitted strict bare JSON 0/8 times (it always wraps in code fences)
  but valid JSON *inside* the fences 8/8, so the parser is deliberately tolerant and the
  feature ships OFF by default.

### Fixed
- `safety._domains` used `lstrip("www.")`, which strips *characters* rather than the
  prefix -- it turned `wonderful.com` into `onderful.com`.

### Changed
- The test suite is sandboxed from the real ledger (conftest), so running tests can never
  append synthetic records to a developer's usage history.

## [0.2.3] - 2026-06-03

### Fixed
- **`cli` backend now retries once on a transient failure.** `claude -p` occasionally
  exits non-zero for a transient reason; previously only the `--bare` case was retried, so
  a blip meant a silently-skipped enhancement. It now retries once (without `--bare`) on any
  fast non-zero exit (timeouts are still fast-failed, never retried). Found by running the
  launcher end-to-end against real `claude`.

### Added
- Live test that drives the **real `enhance` launcher** end-to-end (enhance first prompt →
  proxy → real `claude`), covering the interactive machinery short of the TUI keystroke loop.

## [0.2.2] - 2026-06-03

### Fixed
- **`cli` backend timed out too early.** The default `timeout` is raised 15s → 30s: a cold
  `claude -p` process legitimately takes longer than 15s, so enhancement silently fell open
  to the original. Found by actually running the (previously never-run) live tests.
- **Length guard rejected good rewrites of very short prompts.** The upper bound is now
  `max(length_ratio_max * len(original), 600 chars)`, so a clarified rewrite of a tiny vague
  prompt (which legitimately grows past 12×) is no longer flagged as a runaway.

### Added
- A live end-to-end test that drives **real `claude` through the proxy** and asserts the
  enhanced, faithful prompt reaches upstream — closing the proxy's live-coverage gap. The
  live suite (`pytest -m live`) is now part of the pre-release checklist.

## [0.2.1] - 2026-06-03

### Fixed
- **`cli` backend "Not logged in" failure.** `cli_bare` (the `--bare` flag) is now **off by
  default**: on current Claude Code versions `--bare` bypasses the interactive login, so the
  default subscription-auth path failed with `exit-1`. The engine now also auto-retries
  without `--bare` if an enabled `--bare` call fails.

### Changed
- `enhance`: a `--` separator now passes everything after it to `claude` verbatim (use
  `-m`/`--message` for a prompt that starts with a dash); new `claude_args` config field /
  `PROMPT_ENHANCER_CLAUDE_ARGS` for persistent claude parameters.
- Proxy: removed the opt-in keep-alive forwarder (it buffered reads, breaking SSE streaming,
  and could resubmit a non-idempotent POST); the raw `Connection: close` relay is the only
  path. Tightened the faithfulness token/preamble heuristics to stop rejecting good rewrites
  of ordinary prose; the heuristic backend no longer alters a leading URL/path/code token.

## [0.2.0] - 2026-06-03

### Added
- Selectable backends: `cli` (local `claude -p`), `api` (Anthropic SDK), and `auto`, plus
  **offline & new backends** — `ollama`, `openai`, a dependency-free `heuristic` no-LLM
  fallback (also the `auto` fallback when no model is reachable), Bedrock/Vertex via
  `api_provider`, and third-party **plugin backends** registered through the
  `prompt_preflight.backends` entry-point group.
- Rewrite **profiles** (`--profile concise|detailed|coding|research`) and an optional
  **clarifying-question flow** that folds your answers back into the prompt.
- Safety pipeline: programmatic **faithfulness check** (the original's hard tokens must
  survive the rewrite), length-ratio guard, defensive output cleanup, **secret redaction**
  (credential-bearing prompts are never sent to the enhancer), optional PII warning,
  opt-in result memoization, and a circuit breaker.
- `--bare` cli mode (faster + extra recursion defense) and token **usage/cost** surfaced
  in `EnhanceResult`, `--json`, and `/stats`.
- Easy JSON config file plus `PROMPT_ENHANCER_*` env overrides and `enhance-cli config`
  (`set`/`unset`/`reset`); `--explain`, `-f/--file`, `--repl`, `--watch`, `enhance init`,
  and `enhance-cli stats`.
- A local enhancing proxy (`ANTHROPIC_BASE_URL`) for true prompt replacement and the
  `enhance` launcher; proxy `/healthz`, `/readyz`, `/version`, `/stats` (with p50/p95
  latency), and `/metrics` (Prometheus); dry-run mode;
  structural `logging` with `--log-level`; opt-in OpenTelemetry spans; a bounded
  enhancement concurrency limit; graceful `SIGTERM`/`SIGINT` shutdown; a request
  body-size cap; and an opt-in structured access log.
- `enhance-cli doctor` self-test; Anthropic prompt caching on the constant system prompt.
- Distribution & ops: Homebrew/Scoop manifests, shell widgets, a Grafana dashboard, a
  GHCR image build-push job, a weekly live-test cron, a mkdocs-material docs site, and a
  `CITATION.cff`.
- `async aenhance()` wrapper; `python -m prompt_enhancer` entry point; `py.typed` marker;
  `Dockerfile` and service templates (`systemd`, `launchd`, Windows Scheduled Task).
- Project docs (`LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  privacy section, issue/PR templates) and CI (ruff, mypy, a 75% coverage gate, wheel
  smoke test, optional/scheduled live tests, an offline eval harness, and a PyPI publish
  workflow using OIDC trusted publishing).

### Changed
- Distribution renamed to **`prompt-preflight`** (`prompt-enhancer` is taken on PyPI);
  the import package and console scripts are unchanged.
- `enhance-proxy` became the `enhance` launcher; the rewrite-to-clipboard CLI moved to
  `enhance-cli`.
- CLI backend now passes the prompt on **stdin** (not argv) and parses
  `--output-format json` instead of scraping stdout — more private and more robust.
- Proxy defaults `upstream_base` to an inherited non-proxy `ANTHROPIC_BASE_URL` so
  enterprise LLM-gateway users are no longer bypassed.
- Single source of truth for the version (`prompt_enhancer.__version__`).

### Fixed
- Hook no longer double-enhances when the proxy runs on a non-default port.
- README config example is valid JSON (the previous `jsonc` example did not parse).

## [0.1.0]

### Added
- Core enhancement engine (`claude -p`, Haiku), `UserPromptSubmit` hook, and the original
  `enhance` clipboard CLI, with fail-open behavior, a recursion guard, and the Windows
  npm-shim resolver.

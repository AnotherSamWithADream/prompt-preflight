# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

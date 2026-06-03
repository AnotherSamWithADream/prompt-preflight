# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `enhance-cli doctor` self-test (verifies the `claude` binary, flags, auth, backend).
- Anthropic prompt caching on the (constant) enhancer system prompt for the API backend.
- Proxy: `/healthz`, `/readyz`, and `/stats` endpoints; opt-in structured access log; a
  bounded enhancement concurrency limit; graceful `SIGTERM`/`SIGINT` shutdown; a request
  body-size cap.
- Launcher: `-m/--message`, `--quiet`, `--version`; non-TTY fallback to `claude -p`.
- `enhance-cli --json`, `enhance-cli config set`, and `--version` on all commands.
- `python -m prompt_enhancer` entry point; `py.typed` marker.
- `Dockerfile` and service templates (`systemd`, `launchd`, Windows Scheduled Task).
- Project docs: `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  privacy/data-handling section, issue/PR templates.
- CI: lint (ruff), type-check (mypy), coverage gate, wheel smoke test, optional live
  tests, and a PyPI publish workflow using OIDC trusted publishing.

### Changed
- Distribution renamed to **`prompt-preflight`** (`prompt-enhancer` is taken on PyPI);
  the import package and console scripts are unchanged.
- CLI backend now passes the prompt on **stdin** (not argv) and parses
  `--output-format json` instead of scraping stdout — more private and more robust.
- Proxy defaults `upstream_base` to an inherited non-proxy `ANTHROPIC_BASE_URL` so
  enterprise LLM-gateway users are no longer bypassed.
- Single source of truth for the version (`prompt_enhancer.__version__`).

### Fixed
- Hook no longer double-enhances when the proxy runs on a non-default port.
- README config example is valid JSON (the previous `jsonc` example did not parse).

## [0.2.0] - 2026-06-02

### Added
- Selectable backends: `cli` (local `claude -p`), `api` (Anthropic SDK), and `auto`.
- Easy JSON config file plus `PROMPT_ENHANCER_*` env overrides and `enhance-cli config`.
- A local enhancing proxy (`ANTHROPIC_BASE_URL`) for true prompt replacement, and the
  `enhance` launcher that enhances your first prompt and opens an interactive session.

### Changed
- `enhance-proxy` became the `enhance` launcher; the rewrite-to-clipboard CLI moved to
  `enhance-cli`.

## [0.1.0]

### Added
- Core enhancement engine (`claude -p`, Haiku), `UserPromptSubmit` hook, and the original
  `enhance` clipboard CLI, with fail-open behavior, a recursion guard, and the Windows
  npm-shim resolver.

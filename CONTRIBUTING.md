# Contributing

Thanks for your interest in improving prompt-preflight!

## Development setup

Requires Python 3.9+ and a working `claude` CLI for the live tests (optional).

```bash
python -m pip install -e ".[dev]"
pre-commit install        # optional: runs ruff/format on commit
```

## Running the checks

```bash
pytest                    # fast, deterministic suite (no network, mocks claude)
ruff check .              # lint
ruff format --check .     # formatting
mypy prompt_enhancer      # type check
```

Live tests (spend a small amount of Haiku usage and require `claude` on PATH). The mocked
suite cannot catch flags/timeouts/lengths that break against the *real* binary, so these
exercise real `claude` for the engine **and the proxy end-to-end**:

```bash
# Windows: set PROMPT_ENHANCER_LIVE_TESTS=1   |   POSIX: export PROMPT_ENHANCER_LIVE_TESTS=1
pytest -m live
```

> **Releasing:** `pytest -m live` is a **required** pre-release step — CI cannot run it (no
> `claude` auth on the runners), so a green mocked suite alone is not sufficient to cut a
> release. Run it (ideally against a clean config) before tagging.

## Guidelines

- **Fail open and stay private.** Enhancement must never block the user or write prompt
  contents to disk/logs by default. New code paths should preserve both properties.
- **No shell strings.** Invoke subprocesses with argument lists; keep prompt text off the
  command line.
- Add tests for new behavior; keep the deterministic suite free of network/process I/O.
- Keep changes focused; update `CHANGELOG.md` under `[Unreleased]`.
- Conventional-commit-style messages are appreciated (`feat:`, `fix:`, `docs:`, …).

## Reporting bugs / requesting features

Use the issue templates. For security issues, see [SECURITY.md](SECURITY.md) — do not file
a public issue.

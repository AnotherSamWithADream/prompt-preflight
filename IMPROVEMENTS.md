# prompt-enhancer — improvements & release-readiness backlog

A consolidated list of 75 suggested fixes and improvements to harden the project and
prepare it for public release and deployment. Each item has a stable ID, a priority,
and (where useful) the file/area it touches.

**Priority legend:** 🔴 release blocker · 🟠 high · 🟡 medium · ⚪ nice-to-have
**Totals:** 🔴 4 · 🟠 16 · 🟡 34 · ⚪ 21  (75 items)

> **Status: all 75 implemented** (v0.2.0). Gates pass: `ruff`, `mypy`, `pytest` (≈75%
> coverage with a 70% CI gate), and the wheel builds as `prompt-preflight`. A few
> ⚪ items are intentionally lightweight: SBOM/sigstore run in `publish.yml`, the dev
> "lock" is `requirements-dev.txt` (+ a `uv` recipe), mutation testing is wired via
> `[tool.mutmut]` (run on demand), and log-rotation is left to the OS (`logrotate`).

---

## Do these first

**Release blockers (🔴):** #17 LICENSE · #18 pyproject metadata · #19 PyPI name · #22 publish workflow.

**Correctness near-blockers (the proxy silently mis-behaves without these):**
#33 gateway bypass · #34 / #54 Claude Code payload-format fragility · #48 config example
doesn't parse · #51 double-enhance on non-default port · #60 hook snippet doesn't ship.

**Privacy first-movers:** #1 prompt in process argv · #71 data-handling doc.

### Suggested sequencing
1. **Release-ready:** blockers (#17–19, #22) + correctness near-blockers (#33, #34, #48, #51, #60) + #1, #10, #11.
2. **Robustness & observability:** proxy/engine hardening (#6–8, #26–32, #35–39) + testing (#13–16, #54–57).
3. **Deployment & community:** service/Docker (#24, #66–70), docs/compliance (#71–75), CI/supply-chain (#62–64).

---

## 1. Packaging & PyPI release

- [x] **#17 🔴 Add a `LICENSE` file** — `pyproject` claims MIT but no license file exists.
- [x] **#18 🔴 Complete `pyproject` metadata** — `authors`, `[project.urls]` (Homepage/Repository/Issues), `classifiers`, `keywords`.
- [x] **#19 🔴 Check PyPI name availability** — `prompt-enhancer` is likely taken; pick a unique distribution name (import package can stay `prompt_enhancer`).
- [x] **#22 🔴 Add `publish.yml`** — build sdist+wheel and publish to PyPI via OIDC trusted publishing on tag/release, with provenance attestations.
- [x] **#60 🟠 Add `MANIFEST.in`/package-data** — `examples/settings.json` is outside the package and won't ship in the wheel, so installed users can't find the hook snippet.
- [x] **#10 🟠 Single source of truth for the version** — duplicated in `pyproject.toml` and `__init__.py` (already drifted 0.1.0↔0.2.0); use `dynamic = ["version"]` or `importlib.metadata`.
- [x] **#61 🟡 Add `__main__.py`** so `python -m prompt_enhancer` works (help/dispatch).
- [x] **#20 ⚪ Add a `py.typed` marker** — you expose `enhance()`/`Config`/`EnhanceResult` as a library API.
- [x] **#21 ⚪ Recommend `pipx install`** for end users (isolated CLI install).

## 2. Security & privacy

- [x] **#1 🟠 Prompt leaks into process argv** — CLI backend runs `claude -p "<prompt>"`, visible to `ps`/`/proc`. Verify `claude -p` can read the prompt from stdin and switch to it. (`engine._run_cli`)
- [x] **#2 🟠 Proxy bind-host safety** — refuse/warn on non-loopback `proxy_host` (an unauthenticated proxy relaying your OAuth token). (`proxy.make_server`)
- [x] **#71 🟠 "Data handling & privacy" doc** — exactly what leaves the machine (prompt → Anthropic via your own auth), no telemetry, what (if anything) touches disk.
- [x] **#72 🟠 Compliance review vs Anthropic Agent SDK branding/ToS** — guidelines restrict third-party "Claude Code" naming; sanity-check name/description.
- [x] **#3 🟠 Add `SECURITY.md` + documented threat model** — handles OAuth token + prompt contents; state trust boundaries and a disclosure path.
- [x] **#4 🟡 Redact secrets in `enhance-cli config show`** and document "never put an API key in the config file" (keep `api_key_env`).
- [x] **#5 🟡 Harden GitHub Actions** — pin actions to commit SHAs, set per-job `permissions`, use OIDC (no long-lived PyPI token).

## 3. Correctness & robustness — engine / backends

- [x] **#11 🟠 Add an `enhance-cli doctor` self-test** — verify the `claude` binary, required flags, auth, and chosen backend. Silent fail-open currently hides breakage.
- [x] **#26 🟠 Parse `--output-format json`** instead of scraping raw stdout — a stray warning/ANSI line is currently treated as the rewrite. (`engine._run_cli`)
- [x] **#12 🟡 Detect & warn on an unsupported Claude Code version** — flags are version-specific.
- [x] **#7 🟡 One retry with backoff on transient failures** (API 429/5xx, transient `claude -p` nonzero) before failing open.
- [x] **#8 🟡 Tune timeouts** — `proxy_upstream_timeout=600s` is very long; add a dedicated enhancement timeout under client limits.
- [x] **#25 🟡 API-backend prompt caching + model-retirement handling** — `cache_control` on the constant system prompt; clear message on `api_model` 404.
- [x] **#27 🟡 Reuse one module-level `anthropic.Anthropic` client** across calls (pooling/latency). (`engine._run_api`)
- [x] **#28 🟡 Cache `_resolve_claude_binary()` and the loaded `Config`** — both do filesystem work on every `enhance()`.
- [x] **#29 🟡 Guard prompt length** — cap/warn on very large prompts before spending a call.
- [x] **#65 ⚪ Runtime "minimum Claude Code version" check** + documented support matrix.
- [x] **#30 ⚪ Promote cross-module private imports** (`engine._resolve_claude_binary`, used by the launcher) into a public `util` module.
- [x] **#31 ⚪ Offer an async `enhance()`** (or a clearly thread-safe sync core) for embedding.

## 4. Correctness & robustness — proxy

- [x] **#6 🟠 Cap concurrent enhancements** (semaphore) — unbounded threads each spawn a ~6s `claude -p`; a burst exhausts resources/quota. (`proxy._Handler`)
- [x] **#32 🟠 Keep-alive / pool upstream connections** instead of `Connection: close` per request — a TLS handshake on every call. (`proxy._forward`)
- [x] **#33 🟠 Default `upstream_base` to the existing `ANTHROPIC_BASE_URL`** when it isn't our proxy — enterprise LLM-gateway users are silently bypassed today.
- [x] **#34 🟠 Make the `<system-reminder>` marker + "last user message" assumptions configurable**, pin with a recorded real payload, and fall back to "the only text block." (`proxy._extract_user_prompt`)
- [x] **#35 🟡 Add a max request-body size limit** (OOM/DoS guard). (`proxy._relay`)
- [x] **#36 🟡 Handle or reject (411) chunked request bodies** — a chunked body currently forwards empty.
- [x] **#37 🟡 Add `/healthz` + `/readyz` and a debug `/stats`** (rewrite count, fail-open count, p50/p95 latency).
- [x] **#38 🟡 Graceful `SIGTERM`/`SIGINT` shutdown** for running as a managed service.
- [x] **#39 🟡 Opt-in structured access log** (ts, model, decision, latency — never prompt text).
- [x] **#40 ⚪ Document the HTTP/2→1.1 downgrade** and which headers pass through vs are stripped.

## 5. Hook

- [x] **#51 🟠 Fix the hook↔proxy port mismatch** — if the proxy runs on a non-default port set by flag (not env), the hook reads `cfg.proxy_port` and won't auto-disable → double-enhancement. Treat any loopback `ANTHROPIC_BASE_URL` as the proxy. (`hook.main`)
- [x] **#52 🟡 Document the per-submit latency** the hook adds to every eligible prompt; consider a stricter default threshold or a visible notice.
- [x] **#53 ⚪ Use the `permission_mode` input** to skip enhancement in `plan` mode / continuations.

## 6. Launcher & CLI UX

- [x] **#9 🟡 Unicode-safe clipboard on Windows** — `clip` mangles non-ASCII; use `Set-Clipboard`/`pyperclip`. (`cli.copy_to_clipboard`)
- [x] **#41 🟡 Add `-m/--message`** to `enhance` — `split_args` mis-parses prompts beginning with `-`. (`launcher.split_args`)
- [x] **#42 🟡 Detect non-TTY stdout in the launcher** and fall back to `claude -p`/warn — interactive claude misbehaves when piped.
- [x] **#43 🟡 `--quiet`** to suppress echoing the enhanced prompt (confidential contexts / CI logs). (`launcher.launch`)
- [x] **#44 ⚪ Add `--version`** to `enhance`, `enhance-cli`, `enhance-hook`.
- [x] **#45 ⚪ `enhance-cli --json`** for scripting (`{original, enhanced, backend, elapsed, error}`).
- [x] **#46 ⚪ Cap the `difflib` summary** for very long prompts (O(n²)). (`cli.summarize_changes`)

## 7. Config

- [x] **#48 🟡 Fix the README config example** — labelled `jsonc` with `//` comments, but `load_config` parses strict JSON (comments → silent default). Ship a real JSON template or switch the file to TOML (`tomllib`, ≥3.11).
- [x] **#47 🟡 Validate enum/range fields** (`backend` ∈ {auto,cli,api}, ports, thresholds) and error instead of silently defaulting. (`config.load_config`)
- [x] **#49 🟡 Add `enhance-cli config set <key> <value>`** so users don't hand-edit JSON.
- [x] **#50 ⚪ Show per-key provenance in `config show`** (default vs file vs env won).

## 8. Testing & quality gates

- [x] **#54 🟠 Recorded real Claude Code request payload as a fixture** — assert targeting picks the human block; guards against format drift the whole proxy depends on.
- [x] **#13 🟠 Integration-test the proxy HTTP layer** — the raw-socket relay, streaming, headers, and `_forward` are untested (stand up a fake upstream).
- [x] **#55 🟡 Golden SSE relay test** — capture a real Anthropic stream, assert byte-for-byte relay; add a slow-drip upstream for incremental flushing.
- [x] **#56 🟡 Property/fuzz** `rewrite_request_body` and `split_args` with malformed/adversarial input.
- [x] **#57 🟡 Cross-platform tests** for the Windows shim resolver and clipboard selection (mock `os.name`/filesystem).
- [x] **#14 🟡 Add coverage** (`pytest-cov`) with a threshold gate in CI.
- [x] **#15 🟡 Add lint/format (`ruff`) and type-check (`mypy`) jobs.**
- [x] **#16 🟡 Manual (`workflow_dispatch`) CI job that runs the live tests** with a secret API key before releases.
- [x] **#58 ⚪ Concurrency test** for the proxy once a cap exists.
- [x] **#59 ⚪ Mutation testing** (`mutmut`) to measure test strength.

## 9. Deployment & operations

- [x] **#24 🟡 Proxy-as-a-service** (umbrella for #37/#38/#67) — `/healthz`, graceful shutdown, and a service template so `--serve-only` runs persistently and is monitored.
- [x] **#66 🟡 Provide a `Dockerfile` + `.dockerignore`** for the proxy (api backend + key) — a clean containerized service with `/healthz` for k8s.
- [x] **#67 🟡 Ship service templates** — `systemd` unit, `launchd` plist, Windows Scheduled Task for `enhance --serve-only`.
- [x] **#68 ⚪ Opt-in Prometheus metrics** for a deployed proxy.
- [x] **#69 ⚪ Log rotation / size cap** for the opt-in log file.
- [x] **#70 ⚪ Document multi-user/shared-proxy operation** (per-request auth passthrough) and quota/cost implications.

## 10. CI/CD & supply chain

- [x] **#62 🟡 Dependabot/renovate** for actions + deps, plus a `pre-commit` config.
- [x] **#63 ⚪ Enable `setup-python` pip caching** in CI and add a dev lockfile (`uv.lock`/`requirements-dev.lock`).
- [x] **#64 ⚪ Generate an SBOM** (CycloneDX) and sign artifacts (sigstore) in the release workflow.

## 11. Docs & community

- [x] **#73 🟡 Prominently note the June 15, 2026 Agent SDK credit change** — every `claude -p`/proxy enhancement draws from the new monthly Agent SDK credit pool on subscription plans.
- [x] **#23 🟡 Add `CHANGELOG.md`, `CONTRIBUTING.md`, issue/PR templates**, and README badges (CI/PyPI/license/Python versions).
- [x] **#74 ⚪ Add `CODE_OF_CONDUCT.md`**, a fuller `CONTRIBUTING.md`, and a docs site (mkdocs-material).
- [x] **#75 ⚪ Add an asciinema/GIF demo** and a one-paragraph "what / why / 30-second start" at the top of the README.

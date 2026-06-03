# prompt-preflight — a prompt pre-flight tool

[![build](https://github.com/AnotherSamWithADream/prompt-preflight/actions/workflows/build.yml/badge.svg)](https://github.com/AnotherSamWithADream/prompt-preflight/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/prompt-preflight.svg)](https://pypi.org/project/prompt-preflight/)
[![Python](https://img.shields.io/pypi/pyversions/prompt-preflight.svg)](https://pypi.org/project/prompt-preflight/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Type a rough prompt; your strong model sees a clear one.** prompt-preflight rewrites
your vague input into a clearer, better-structured prompt with **Claude Haiku** before it
reaches a stronger model — so you spend Opus/Sonnet tokens on a good prompt, not a messy
one. Run `enhance "make my code faster"` and an interactive Claude session opens with the
*enhanced* prompt already in flight. **30-second start:** `pip install prompt-preflight`
→ `enhance "your rough prompt"`.

> Unofficial, community tool. Not affiliated with or endorsed by Anthropic; "Claude" and
> "Claude Code" are trademarks of Anthropic. It reuses *your own* Claude auth and adds no
> telemetry — see [Privacy](#privacy--data-handling).

📖 **Full documentation:** <https://anothersamwithadream.github.io/prompt-preflight/> —
Quickstart, Backends, Proxy, Deploy, Security, and Architecture.

One core engine, two selectable backends, and **three ways to use it**:

| # | Surface | What the strong model sees | Setup |
|---|---|---|---|
| 1 | **`enhance`** — enhance your prompt, then interactive claude (proxy enhances follow-ups) | **Only the enhanced prompt** ✅ | `enhance "your prompt"` |
| 2 | **Hook** (`enhance-hook`) | your original **+** an enhanced restatement | one `settings.json` snippet |
| 3 | **`enhance-cli`** — rewrite a prompt to the clipboard | only what you paste (manual) | run it, paste result |

**Backends:** `cli` (local `claude -p`, reuses your Claude Code auth, **no API key**),
`api` (Anthropic SDK + `ANTHROPIC_API_KEY`), or **`auto`** (API if a key is present,
else CLI). All selectable in one config file.

> **Why three?** The goal is "send normally → the strong model sees only the enhanced
> prompt." A Claude Code **hook physically can't replace your prompt** (only append or
> block — confirmed below; it's an open feature request). The only way to get *only the
> enhanced prompt* into an interactive session is the **proxy**, which rewrites the
> actual API request. The hook is the zero-setup fallback. Other published tools
> ([prompt-improver](https://github.com/severity1/claude-code-prompt-improver),
> [prompt-enhancer](https://github.com/scooter-lacroix/claude-code-prompt-enhancer),
> [prompt-optimizer](https://github.com/johnpsasser/claude-code-prompt-optimizer), …) all
> use the append-only hook — none achieve true replacement.

---

## How it works against Claude Code (2.1.x)

- **The `UserPromptSubmit` hook cannot replace the prompt.** Its only outputs are
  `decision:"block"`, `reason`, `hookSpecificOutput.additionalContext` (append),
  `sessionTitle`, `suppressOriginalPrompt` — **no `updatedPrompt`** ([hooks ref](https://code.claude.com/docs/en/hooks),
  feature requests [#53330](https://github.com/anthropics/claude-code/issues/53330),
  [#46761](https://github.com/anthropics/claude-code/issues/46761)). So the hook appends
  a labelled "Clarified restatement"; the **proxy** does true replacement.
- **`claude -p` flags:** `--model haiku` ✓, `--system-prompt` (replace — *not*
  `--append-system-prompt`, which makes Haiku *answer* instead of rewrite) ✓,
  `--max-turns 1` ✓, `--tools ""` to disable all tools ✓.
- **The proxy works with subscription auth.** Under `claude.ai` auth, Claude Code forwards
  its `Authorization` header to a local `ANTHROPIC_BASE_URL` proxy, which relays it upstream
  unchanged — so the strong model receives only the rewritten prompt.

---

## Install

Requires Python 3.9+ and a working `claude` CLI (`claude --version`; Claude Code ≥ 2.1).

```bash
pipx install prompt-preflight        # recommended: isolated CLI install
# or:  pip install prompt-preflight
# one-shot, no install:
uvx prompt-preflight --help          # via uv
pipx run prompt-preflight --help     # via pipx
# from a clone, for development:
pip install -e .            # commands: enhance, enhance-cli, enhance-hook
pip install -e ".[api]"     # also installs the `anthropic` SDK for the api backend
```

> **Windows note.** When Claude Code is installed via npm, `claude` is a `.cmd`/`.ps1`
> shim Python's `subprocess` can't launch (and routing through `cmd.exe` would re-parse
> your prompt — an injection risk). The engine auto-resolves the shim to the bundled
> `…\node_modules\@anthropic-ai\claude-code\bin\claude.exe`. Override with
> `PROMPT_ENHANCER_CLAUDE_BIN` if needed.

---

## Backends & config

One JSON file, plus `PROMPT_ENHANCER_*` env overrides. Manage it with:

```bash
enhance-cli config show              # print effective config + where it's loaded from
enhance-cli config path              # print the config file path
enhance-cli config init              # write a default config you can edit
enhance-cli config edit              # open it in $EDITOR (notepad on Windows)
enhance-cli config set backend api   # set a single key without hand-editing JSON
enhance-cli doctor                   # verify the binary, flags, auth, and backend work
```

Lookup order: `$PROMPT_ENHANCER_CONFIG` → `./.prompt-enhancer.json` →
`%APPDATA%\prompt-enhancer\config.json` (Windows) / `~/.config/prompt-enhancer/config.json`.

The config file is **plain JSON** (no comments). Key fields, with defaults:

```json
{
  "backend": "auto",
  "model": "haiku",
  "api_model": "claude-haiku-4-5",
  "api_key_env": "ANTHROPIC_API_KEY",
  "timeout": 15.0,
  "word_threshold": 12,
  "bypass_prefix": "//raw",
  "proxy_port": 8788,
  "proxy_skip_models": ["haiku"],
  "proxy_require_tools": true
}
```

| field | meaning |
|---|---|
| `backend` | `"auto"` \| `"cli"` \| `"api"` |
| `model` / `api_model` | the Haiku model for the cli / api backend |
| `timeout` | seconds before fail-open |
| `word_threshold` | prompts shorter than this pass through unchanged |
| `proxy_skip_models` | request models that are never enhanced |
| `proxy_require_tools` | only enhance the main agentic turn (which carries tools) |

Every field has a `PROMPT_ENHANCER_<FIELD>` env override (run `enhance-cli config show`
to see which are active). `auto` is right for most people: with no API key it uses your
Claude Code login via `claude -p`; drop an `ANTHROPIC_API_KEY` in and it switches to the
(faster) API.

---

## 1. `enhance` — enhance your first prompt, then an interactive Claude session

```bash
enhance "make my code faster and add tests"   # enhance this prompt, open claude with it
enhance                                        # no prompt: just launch claude (enhanced as you type)
enhance "fix the login bug" --model opus       # leading words = prompt; flags go to claude
enhance --model opus -- "fix the login bug"    # or put the prompt after `--`
```

`enhance "..."` rewrites your prompt with Haiku and starts an **interactive** `claude`
whose first message is the enhanced prompt. A local proxy also runs, so **follow-up
prompts you type in the session are enhanced too** (the first prompt is skipped so it
isn't enhanced twice). When claude exits, the proxy stops; if the configured port is busy,
a free one is chosen automatically.

- A `//raw` prefix or a leading `/` (slash command) on the prompt skips enhancement.
- `enhance` with no prompt (or only flags) launches claude through the proxy, which
  enhances whatever you type first.
- `enhance --serve-only` runs just the proxy (no claude), to point a separate claude
  session at it.

Under the hood the proxy intercepts each API request and **rewrites only your main
prompt**. How it targets correctly (a single Claude Code turn makes several API calls):

- **Skips** requests whose model is in `proxy_skip_models` (default: anything `haiku` —
  Claude Code's background/title calls).
- **Skips** requests without a tool list (`proxy_require_tools`) — only the main agentic
  turn carries tools.
- **Rewrites** the one human text block, leaving any `<system-reminder>` context blocks
  untouched. Tool-loop turns (`tool_result`) are left alone.
- Honors `//raw`, the word threshold, and slash-command skipping; **fails open** (on any
  error the original request is forwarded unchanged).
- The response is relayed **raw**, so streaming/SSE is preserved byte-for-byte.

**Cost:** the enhancement runs before the strong model starts, adding the rewrite latency
to your first token (~5 s with the `cli` backend, ~1–3 s with `api`). Set
`PROMPT_ENHANCER_PROXY_DEBUG=1` to log per-request *structural* decisions (never prompt
text).

The proxy and hook are mutually exclusive by design — **the hook auto-disables whenever
`ANTHROPIC_BASE_URL` points at the proxy**, so they never double-enhance. If the proxy
ever doesn't suit you, just don't set `ANTHROPIC_BASE_URL` and the hook takes over.

---

## 2. The hook — zero-setup fallback (append)

Merge `examples/settings.json` into `~/.claude/settings.json` (global) or a project's
`.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "enhance-hook", "timeout": 20 } ] }
    ]
  }
}
```

(No `pip install`? Use `"command": "python \"<abs path>\\prompt_enhancer\\hook.py\""`.)

It injects the rewrite as **`additionalContext`** labelled *"Clarified restatement of the
user's request"*, instructing the model to follow it while honouring your original on any
conflict. It skips slash commands, short prompts (< `word_threshold` words), and `//raw`,
and **fails open** (emits nothing → your prompt proceeds unchanged). Because the platform
can't strip text, a leading `//raw` token stays visible to the model (the CLI strips it).

---

## 3. `enhance-cli` — desktop / web app workflow

```bash
enhance-cli "make my code faster and add some tests"   # or pipe via stdin
```

Rewrites, prints a short **"Changes made"** summary, and asks **[A]ccept / [E]dit /
[R]eject** before copying to your clipboard (`pbcopy` / `clip` / `wl-copy` / `xclip` /
`xsel`). Edit opens `$EDITOR` on a temp file (deleted immediately after). Reject copies
your **original** instead. `//raw …` skips enhancement and copies your text verbatim
(token stripped). Flags: `-y/--yes`, `--no-clipboard`, `--diff`.

---

## The enhancer system prompt

Lives in [`prompt_enhancer/system_prompt.py`](prompt_enhancer/system_prompt.py): rewrite
(don't answer); **faithfulness first** (preserve every specific, invent nothing); improve
clarity/structure; return already-clear prompts ~unchanged; append a short **"Open
questions"** list only when genuinely ambiguous. Tuned and stress-tested live so it never
slips into a conversational reply, even on an ultra-vague prompt.

---

## Privacy & data handling

**What leaves your machine:** only your prompt, sent to Anthropic to be rewritten —
through your own `claude` CLI auth (cli backend) or your `ANTHROPIC_API_KEY` (api backend)
— and then your enhanced prompt to your chosen model. **Nothing else. No telemetry.**

- **Nothing on disk by default.** Opt-in, local-only diagnostics: `PROMPT_ENHANCER_LOG=<path>`
  logs **metadata only** (timings, sizes, fail-open reasons); add `PROMPT_ENHANCER_LOG_CONTENT=1`
  to also record prompt text. Proxy debug (`PROMPT_ENHANCER_PROXY_DEBUG=1`) logs only request
  *structure*, never prompt text.
- **Prompt never on the command line.** The cli backend sends the prompt on **stdin**, so it
  isn't visible in `ps` / `/proc`, and parses a `--output-format json` envelope rather than
  scraping stdout.
- **The tool never handles your credentials.** `claude` manages its own auth; the proxy
  passes your `Authorization` header upstream unchanged and never logs it.
- See [SECURITY.md](SECURITY.md) for the full threat model and how to report issues.

## Safety

- **Fail open** (~15 s, configurable): timeout, non-zero exit, missing binary, missing key,
  bad/empty output → your **original** text, unchanged. Run `enhance-cli doctor` to confirm
  enhancement is actually working (silent fail-open otherwise looks like a no-op).
- **No shell / no injection.** `claude` runs via an argv list (and on Windows the resolved
  `.exe`, never the `.cmd` shim); the prompt goes on stdin or in a JSON body, never on a
  command line.
- **Recursion-safe.** The engine sets `PROMPT_ENHANCER_ACTIVE=1` for its child; the hook
  bails the moment it sees that (or any loopback `ANTHROPIC_BASE_URL`); and the enhancement
  call never loops through the proxy.
- **Proxy bind-safety.** The proxy is unauthenticated and binds to `127.0.0.1`; it refuses a
  non-loopback host unless you set `allow_public_bind` / `PROMPT_ENHANCER_ALLOW_PUBLIC_BIND=1`.
  It also caps request body size and concurrent enhancements.

> **Cost note (changes 2026-06-15):** every enhancement is a `claude -p` / API call. From
> June 15 2026, `claude -p` and Agent SDK usage on subscription plans draws from a new
> **monthly Agent SDK credit pool**, separate from interactive limits. Budget accordingly,
> or use the `api` backend with your own key.

### Environment variables

Every config field has a `PROMPT_ENHANCER_<FIELD>` override (e.g. `PROMPT_ENHANCER_BACKEND`,
`PROMPT_ENHANCER_PROXY_PORT`, `PROMPT_ENHANCER_ALLOW_PUBLIC_BIND`). Diagnostics:
`PROMPT_ENHANCER_LOG`, `PROMPT_ENHANCER_LOG_CONTENT`, `PROMPT_ENHANCER_PROXY_DEBUG`,
`PROMPT_ENHANCER_PROXY_ACCESS_LOG`. Run `enhance-cli config show` to see what's active.

---

## Tests

```bash
pip install -e ".[dev]"
pytest                 # fast, deterministic; never calls claude (mocked)
```

Covers: faithfulness/expansion intent, `//raw` bypass, fail-open on `claude -p` error, the
recursion guard, backend selection, the API backend, config loading/overrides, the shared
policy, and the proxy's request-rewriting (model/tools/reminder/tool-result targeting).

Opt-in live tests (real model):

```bash
set PROMPT_ENHANCER_LIVE_TESTS=1   # Windows;  export ... on POSIX
pytest -m live
```

CI runs `ruff`, `mypy`, and the suite (with a coverage gate) across
ubuntu/macos/windows × Python 3.9–3.13.

---

## Deploy the proxy as a shared service

For a team, run one enhancing proxy (api backend) and point several Claude Code sessions
at it — each user's own `Authorization` header is passed through, so it serves everyone
while enhancement uses the proxy's key.

```bash
docker build -t prompt-preflight .
docker run --rm -p 8788:8788 -e ANTHROPIC_API_KEY=sk-... prompt-preflight
# then, per user:  ANTHROPIC_BASE_URL=http://<host>:8788 claude
```

`GET /healthz`, `/readyz`, and `/stats` are exposed for monitoring; `SIGTERM` shuts down
gracefully. Service templates for `enhance --serve-only` live in [`deploy/`](deploy/)
(systemd, launchd, Windows Scheduled Task).

---

## Project layout

```
prompt_enhancer/
  __init__.py        engine.py        # core: backends (cli/api/auto), fail-open, recursion guard
  config.py          system_prompt.py # config file + env;   the enhancer prompt
  policy.py          hook.py          # shared enhance/skip/raw decision;  UserPromptSubmit hook
  proxy.py           launcher.py      # enhancing ANTHROPIC_BASE_URL proxy;  `enhance` (proxy + claude)
  cli.py                              # `enhance-cli` (rewrite to clipboard) + `enhance-cli config`
tests/               examples/settings.json
conftest.py  pyproject.toml  README.md
```

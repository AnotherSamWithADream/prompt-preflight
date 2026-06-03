# prompt-preflight

[![build](https://github.com/AnotherSamWithADream/prompt-preflight/actions/workflows/build.yml/badge.svg)](https://github.com/AnotherSamWithADream/prompt-preflight/actions/workflows/build.yml)
[![PyPI](https://img.shields.io/pypi/v/prompt-preflight.svg)](https://pypi.org/project/prompt-preflight/)
[![Python](https://img.shields.io/pypi/pyversions/prompt-preflight.svg)](https://pypi.org/project/prompt-preflight/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://anothersamwithadream.github.io/prompt-preflight/)

**Type a rough prompt; your strong model sees a clear one.**

prompt-preflight runs a fast *pre-flight* pass with **Claude Haiku** that sharpens your
vague input into a clear, well-structured prompt — **without inventing requirements** —
before it reaches a stronger model. You write the way you always do; Opus/Sonnet spends
its budget on a good prompt instead of a messy one.

```text
  you type:   "make my api faster"
                      │
              ┌───────▼────────┐
              │ prompt-preflight│  ~1s, a fraction of a cent (Haiku)
              └───────┬────────┘
                      │
  model sees: "Profile and optimize my REST API for latency. Identify the slowest
               endpoints, reduce N+1 queries, add caching where safe, and measure
               before/after. Keep the existing public routes unchanged."
```

If anything goes wrong, it **fails open** to your original text — the worst case is a
one-second delay, never a blocked or mangled prompt.

> Unofficial, community tool. Not affiliated with or endorsed by Anthropic; "Claude" and
> "Claude Code" are trademarks of Anthropic. It reuses *your own* Claude auth and adds **no
> telemetry** — see [Privacy](#privacy--data-handling).

📖 **Full docs:** <https://anothersamwithadream.github.io/prompt-preflight/>

---

## Contents

- [Quick start](#quick-start)
- [Which path is right for you?](#which-path-is-right-for-you)
- [Backends](#backends)
- [1. `enhance` — interactive Claude Code (true replacement)](#1-enhance--interactive-claude-code-true-replacement)
- [2. The hook — zero-setup fallback](#2-the-hook--zero-setup-fallback)
- [3. `enhance-cli` — any desktop / web app](#3-enhance-cli--any-desktop--web-app)
- [Configuration](#configuration)
- [Command cheat-sheet](#command-cheat-sheet)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Privacy & data handling](#privacy--data-handling)
- [Safety](#safety)
- [How it works against Claude Code](#how-it-works-against-claude-code)
- [Develop & test](#develop--test)

---

## Quick start

**Requirements:** Python 3.9+. For the default zero-config path, a working `claude` CLI
(`claude --version`; Claude Code ≥ 2.1). No API key needed — it reuses your Claude login.

```bash
pip install prompt-preflight       # or: pipx install prompt-preflight (isolated)
```

Try it in one line — rewrite a prompt and copy the result to your clipboard:

```bash
enhance-cli "make my code faster and add tests"
```

Or, for **interactive Claude Code**, enhance your prompt *and* open a session with it:

```bash
enhance "make my code faster and add tests"
```

Not sure it's wired up correctly? Run the self-check:

```bash
enhance-cli doctor
```

<details>
<summary>Other install options (uvx, pipx run, from source, the <code>api</code> extra)</summary>

```bash
# one-shot, no install:
uvx prompt-preflight --help          # via uv
pipx run prompt-preflight --help     # via pipx

# from a clone, for development:
pip install -e .            # commands: enhance, enhance-cli, enhance-hook
pip install -e ".[api]"     # also installs the Anthropic SDK for the api backend
```
</details>

---

## Which path is right for you?

There are three ways to use prompt-preflight. Pick by **where you type your prompts**:

| If you… | Use | What the strong model sees | Setup |
|---|---|---|---|
| use **interactive Claude Code** | **`enhance`** (proxy) | **only the enhanced prompt** ✅ | `enhance "your prompt"` |
| want **zero setup** inside Claude Code | **hook** (`enhance-hook`) | your original **+** a clarified restatement | one settings.json line |
| use the **desktop/web app** (or any tool) | **`enhance-cli`** | only what you paste | run it, paste the result |

**Most people want `enhance`.** It's the only path that achieves true *replacement* in an
interactive session (the proxy rewrites the actual API request). The hook is the
no-install fallback; `enhance-cli` is for everything outside Claude Code.

> **Why can't the hook just replace the prompt?** Claude Code's `UserPromptSubmit` hook can
> only *append* context or *block* — it has no "replace my prompt" output (see
> [How it works](#how-it-works-against-claude-code)). The proxy is what makes true
> replacement possible.

---

## Backends

One engine, several interchangeable backends. Set `backend` in config (or
`PROMPT_ENHANCER_BACKEND`). The default, **`auto`**, is right for most people.

| Backend | How it works | Needs | Good for |
|---|---|---|---|
| **`auto`** *(default)* | API key → `api`; else `claude` CLI → `cli`; else `heuristic` | nothing | just works |
| `cli` | `claude -p` with Haiku, prompt on **stdin** | Claude Code CLI | **no API key** — reuses your login |
| `api` | Anthropic SDK, system-prompt caching | `prompt-preflight[api]` + key | speed; servers |
| `openai` | OpenAI-compatible chat completions | `openai` SDK + key | non-Anthropic models |
| `ollama` | local Ollama chat endpoint | a running Ollama | **fully offline**, free |
| `heuristic` | rule-based cleanup, **no model** | nothing | air-gapped; last resort |

- **Offline / private:** `ollama` (a real local model) or `heuristic` (no model at all —
  it only tidies whitespace, casing, and punctuation, so it can never change your meaning).
- **Enterprise:** set `api_provider` to `bedrock` or `vertex` to use AWS/GCP credentials
  instead of an `ANTHROPIC_API_KEY`.
- **Plugins:** register your own backend via the `prompt_preflight.backends` entry-point
  group — no need to fork. A misbehaving plugin fails open. (See the
  [Backends docs](https://anothersamwithadream.github.io/prompt-preflight/backends/).)

**Profiles** tune the rewrite for a task. Pass `--profile` or set `profile` in config:

```bash
enhance-cli --profile coding "speed up the parser"     # preserves code/paths/identifiers
enhance-cli --profile concise "..."                    # shortest faithful rewrite
```

Choices: `default`, `concise`, `detailed`, `coding`, `research`.

---

## 1. `enhance` — interactive Claude Code (true replacement)

```bash
enhance "make my code faster and add tests"   # enhance this prompt, then open claude with it
enhance                                        # no prompt: open claude, enhance as you type
enhance "fix the login bug" --model opus       # leading words = prompt; flags pass to claude
enhance --model opus -- "fix the login bug"    # or put the prompt after `--`
enhance --serve-only                           # run just the proxy (point your own claude at it)
```

`enhance "..."` rewrites your prompt with Haiku and starts an **interactive** `claude`
whose first message is the enhanced prompt. A local proxy runs alongside it, so **the
follow-up prompts you type in the session are enhanced too** (your already-enhanced first
prompt is skipped, so it's never enhanced twice). When claude exits, the proxy stops; if
the configured port is busy, a free one is chosen automatically.

The proxy is conservative — a single Claude Code turn makes several API calls, and it
rewrites **only your main prompt**:

- **Skips** background/title calls (Haiku, in `proxy_skip_models`) and utility turns
  without a tool list (`proxy_require_tools`).
- **Rewrites** the one human text block, leaving `<system-reminder>` context untouched;
  tool-loop turns (`tool_result`) are left alone.
- Honors `//raw`, the word threshold, and slash commands; **fails open** on any error
  (the request is forwarded unchanged). Responses relay **raw**, so streaming is preserved.

The proxy and hook never double-enhance: **the hook auto-disables whenever
`ANTHROPIC_BASE_URL` points at the proxy.**

> **Cost & latency:** enhancement runs before the strong model starts, adding the rewrite
> time to your first token (~5 s with `cli`, ~1–3 s with `api`). Set
> `PROMPT_ENHANCER_PROXY_DEBUG=1` to log per-request *structural* decisions (never prompt
> text).

---

## 2. The hook — zero-setup fallback

The fastest way to install the hook:

```bash
enhance-cli init      # adds enhance-hook to ~/.claude/settings.json (with a backup)
```

Or merge this into `~/.claude/settings.json` (global) or a project's `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "enhance-hook", "timeout": 20 } ] }
    ]
  }
}
```

It injects the rewrite as **`additionalContext`** — a labelled *"Clarified restatement"*
that the model follows while honouring your original on any conflict. It skips slash
commands, short prompts (< `word_threshold` words), and `//raw`, and **fails open** (emits
nothing → your prompt proceeds unchanged). Prefer a terser one-line framing? Set
`hook_output_style` to `minimal`.

> No `pip install`? Use `"command": "python \"<abs path>/prompt_enhancer/hook.py\""`.

---

## 3. `enhance-cli` — any desktop / web app

```bash
enhance-cli "make my code faster and add some tests"   # or pipe text via stdin
```

It rewrites your prompt, prints a short **"Changes made"** summary, and asks **[A]ccept /
[E]dit / [R]eject** before copying the result to your clipboard (`pbcopy` / `clip` /
`wl-copy` / `xclip` / `xsel`). Edit opens `$EDITOR` on a temp file (deleted immediately
after); Reject copies your **original** instead.

Handy flags:

| Flag | Effect |
|---|---|
| `-y`, `--yes` | accept without prompting |
| `-f`, `--file FILE` | read the prompt from a file |
| `--profile NAME` | use a rewrite profile (see [Backends](#backends)) |
| `--diff` | also show a unified diff of the changes |
| `--json` | print a structured result (for scripting) and exit |
| `--explain` | print why it did or didn't enhance (backend, error, timing) |
| `--repl` | interactive loop: type a prompt, get the rewrite, repeat |
| `--watch` | rewrite whatever you copy to the clipboard, in place |
| `--no-clipboard` | don't touch the clipboard; just print to stdout |
| `//raw …` | bypass enhancement and copy your text verbatim (token stripped) |

When a rewrite raises **"Open questions"**, the CLI offers to let you answer them and folds
your answers back into the prompt in one pass.

---

## Configuration

Everything is configurable via **one JSON file** plus `PROMPT_ENHANCER_*` env overrides.
You rarely need to touch it — `auto` is sensible out of the box.

```bash
enhance-cli config show              # effective config + where it's loaded from + active env overrides
enhance-cli config path              # the config file path
enhance-cli config init              # write a default config you can edit
enhance-cli config edit              # open it in $EDITOR
enhance-cli config set backend api   # set one key without hand-editing JSON
enhance-cli config unset backend     # remove one key
enhance-cli config reset             # delete the config file
```

**Lookup order:** `$PROMPT_ENHANCER_CONFIG` → `./.prompt-enhancer.json` →
`%APPDATA%\prompt-enhancer\config.json` (Windows) / `~/.config/prompt-enhancer/config.json`.

The file is **plain JSON** (no comments). Common fields, with defaults:

```json
{
  "backend": "auto",
  "profile": "default",
  "timeout": 15.0,
  "word_threshold": 12,
  "bypass_prefix": "//raw",
  "api_model": "claude-haiku-4-5",
  "api_key_env": "ANTHROPIC_API_KEY",
  "proxy_port": 8788,
  "proxy_skip_models": ["haiku"],
  "proxy_require_tools": true
}
```

| Field | Meaning |
|---|---|
| `backend` | `auto` \| `cli` \| `api` \| `openai` \| `ollama` \| `heuristic` (or a plugin name) |
| `profile` | rewrite style: `default` \| `concise` \| `detailed` \| `coding` \| `research` |
| `timeout` | seconds before fail-open |
| `word_threshold` | prompts shorter than this pass through unchanged |
| `bypass_prefix` | the skip token (default `//raw`) |
| `proxy_skip_models` | request models that are never enhanced (Claude Code's background calls) |
| `proxy_require_tools` | only enhance the main agentic turn (which carries tools) |
| `api_provider` | `anthropic` \| `bedrock` \| `vertex` |
| `hook_output_style` | `context` (labelled block) \| `minimal` (one line) |

**Every field has a `PROMPT_ENHANCER_<FIELD>` env override** (e.g.
`PROMPT_ENHANCER_BACKEND=api`, `PROMPT_ENHANCER_PROFILE=coding`,
`PROMPT_ENHANCER_PROXY_PORT=9000`). Run `enhance-cli config show` to see which are active.
The full field list is in the [docs](https://anothersamwithadream.github.io/prompt-preflight/).

---

## Command cheat-sheet

```text
# Interactive Claude Code (proxy + launcher)
enhance "prompt"               enhance the prompt, then open claude with it
enhance                        open claude; the proxy enhances what you type
enhance -m "prompt" --quiet    enhance a prompt without echoing the rewrite
enhance --serve-only           run only the proxy
enhance "fix bug" --model opus pass flags through to claude (prompt = leading words)

# Rewrite to clipboard / scripting
enhance-cli "prompt"           rewrite → Accept/Edit/Reject → clipboard
enhance-cli -y --diff "..."    auto-accept and show a diff
enhance-cli --json "..."       structured output for scripts
enhance-cli --repl             interactive rewrite loop
enhance-cli --watch            rewrite clipboard text as you copy it
enhance-cli //raw "..."        bypass enhancement (copy verbatim)

# Setup, diagnostics, ops
enhance-cli init               install the hook into ~/.claude/settings.json
enhance-cli doctor             verify binary, flags, auth, and backend
enhance-cli config show        view effective config
enhance-cli stats              pretty-print a running proxy's /stats
```

---

## Troubleshooting & FAQ

**"It doesn't seem to do anything."**
Run `enhance-cli doctor` — it checks the `claude` binary, flags, auth, and runs one live
enhancement. A silent fail-open (e.g. missing binary) looks like a no-op; `--explain` on
`enhance-cli` prints the exact reason.

**"My prompt got enhanced twice."**
It shouldn't — the hook auto-disables when the proxy is active. If you see it, you likely
have *both* the hook installed *and* `ANTHROPIC_BASE_URL` set to something non-loopback.
Use one path at a time.

**"It's slow."**
The `cli` backend adds ~5 s (it launches `claude`). The `api` backend is ~1–3 s — set
`PROMPT_ENHANCER_BACKEND=api` with an `ANTHROPIC_API_KEY`. The `cli` backend already uses
`--bare` to skip hook/skill/MCP discovery.

**"I want to skip enhancement for one prompt."**
Prefix it with `//raw` (or use a `/slash-command`, which is always skipped).

**"Turn it off entirely."**
Set `PROMPT_ENHANCER_DISABLE=1` — honored by the engine, hook, proxy, and launcher.

**"The proxy port is busy."**
`enhance` picks a free port automatically. For `--serve-only`, set `proxy_port` or
`PROMPT_ENHANCER_PROXY_PORT`.

**"I'm on Windows and `claude` isn't found."**
When Claude Code is installed via npm, `claude` is a `.cmd`/`.ps1` shim that Python can't
launch directly (and routing through `cmd.exe` would re-parse your prompt — an injection
risk). The engine auto-resolves it to the bundled
`…\node_modules\@anthropic-ai\claude-code\bin\claude.exe`. Override with
`PROMPT_ENHANCER_CLAUDE_BIN` if needed.

**"Does it work with my Claude subscription (no API key)?"**
Yes. The `cli` backend reuses your Claude Code login. The proxy also works under `claude.ai`
auth — Claude Code forwards its `Authorization` header to the local proxy, which relays it
upstream unchanged.

---

## Privacy & data handling

**What leaves your machine:** only your prompt, sent to be rewritten — through your own
`claude` CLI auth (`cli`), your `ANTHROPIC_API_KEY` (`api`), your chosen provider
(`openai`), your **local** Ollama (`ollama`), or **nothing at all** (`heuristic`) — and
then your enhanced prompt to your model. **Nothing else. No telemetry.**

- **Nothing on disk by default.** Diagnostics are opt-in and local-only:
  `PROMPT_ENHANCER_LOG=<path>` logs **metadata only** (timings, sizes, fail-open reasons);
  add `PROMPT_ENHANCER_LOG_CONTENT=1` to also record prompt text. Proxy debug
  (`PROMPT_ENHANCER_PROXY_DEBUG=1`) and `--log-level` log only request *structure*, never
  prompt text.
- **Credentials never reach the enhancer.** Before rewriting, prompts are scanned for
  secrets (API keys, `Bearer …`, AWS keys); if one is found the prompt is **not** sent to
  the enhancer — your original still proceeds to the strong model.
- **Prompt never on the command line.** The `cli` backend sends the prompt on **stdin**, so
  it isn't visible in `ps`/`/proc`, and parses a JSON envelope rather than scraping stdout.
- **The tool never handles your credentials.** `claude` manages its own auth; the proxy
  passes your `Authorization` header upstream unchanged and never logs it.

See [SECURITY.md](SECURITY.md) for the threat model and how to report issues.

---

## Safety

- **Fail open** (~15 s, configurable): timeout, non-zero exit, missing binary/key,
  bad/empty output, dropped specifics, implausible length, a crashing plugin, a tripped
  circuit breaker → your **original** text, unchanged.
- **Faithful or nothing.** A programmatic check verifies your hard tokens (file paths,
  URLs, code spans, numbers) survive the rewrite; if any are dropped, it fails open.
- **No shell / no injection.** `claude` runs via an argv list (and on Windows the resolved
  `.exe`, never the `.cmd` shim); the prompt goes on stdin or in a JSON body, never on a
  command line.
- **Recursion-safe.** The engine sets `PROMPT_ENHANCER_ACTIVE=1` for its child; the hook
  bails the moment it sees that (or any loopback `ANTHROPIC_BASE_URL`).
- **Proxy bind-safety.** The proxy binds to `127.0.0.1` and refuses a non-loopback host
  unless you set `allow_public_bind` / `PROMPT_ENHANCER_ALLOW_PUBLIC_BIND=1`. It caps
  request body size and concurrent enhancements.

> **Cost note (changes 2026-06-15):** every enhancement is a `claude -p` / API call. From
> June 15 2026, `claude -p` and Agent SDK usage on subscription plans draws from a new
> **monthly Agent SDK credit pool**, separate from interactive limits. Budget accordingly,
> or use the `api` backend with your own key.

---

## How it works against Claude Code

The technical details that make true replacement possible (verified against Claude Code 2.1.x):

- **The `UserPromptSubmit` hook cannot replace the prompt.** Its only outputs are
  `decision:"block"`, `reason`, `hookSpecificOutput.additionalContext` (append),
  `sessionTitle`, `suppressOriginalPrompt` — **no `updatedPrompt`**
  ([hooks ref](https://code.claude.com/docs/en/hooks), feature requests
  [#53330](https://github.com/anthropics/claude-code/issues/53330),
  [#46761](https://github.com/anthropics/claude-code/issues/46761)). So the hook *appends*
  a labelled restatement; the **proxy** does true replacement.
- **`claude -p` flags:** `--model haiku` ✓, `--system-prompt` (replace — *not*
  `--append-system-prompt`, which makes Haiku *answer* instead of rewrite) ✓,
  `--max-turns 1` ✓, `--tools ""` to disable all tools ✓.
- **The proxy works with subscription auth.** Under `claude.ai` auth, Claude Code forwards
  its `Authorization` header to a local `ANTHROPIC_BASE_URL` proxy, which relays it upstream
  unchanged — so the strong model receives only the rewritten prompt.

Other published tools
([prompt-improver](https://github.com/severity1/claude-code-prompt-improver),
[prompt-enhancer](https://github.com/scooter-lacroix/claude-code-prompt-enhancer),
[prompt-optimizer](https://github.com/johnpsasser/claude-code-prompt-optimizer)) all use
the append-only hook — none achieve true replacement.

The enhancer system prompt lives in
[`prompt_enhancer/system_prompt.py`](prompt_enhancer/system_prompt.py): rewrite (don't
answer), faithfulness first, improve clarity/structure, return already-clear prompts
~unchanged, and append a short **"Open questions"** list only when genuinely ambiguous.

---

## Develop & test

```bash
pip install -e ".[dev]"
pytest                 # fast, deterministic; never calls claude (mocked)
ruff check . && mypy prompt_enhancer
```

Opt-in live tests (real model):

```bash
set PROMPT_ENHANCER_LIVE_TESTS=1   # Windows;  export ... on POSIX
pytest -m live
```

CI runs `ruff`, `mypy`, and the suite (with a coverage gate) across
ubuntu/macOS/windows × Python 3.9–3.13. Deploying the proxy as a shared service? See the
[Deploy docs](https://anothersamwithadream.github.io/prompt-preflight/deploy/) and the
templates in [`deploy/`](deploy/).

### Project layout

```text
prompt_enhancer/
  engine.py        # backend dispatch, safety pipeline, fail-open, caching, breaker
  safety.py        # secret/PII detection, token & length guards, output cleanup
  config.py        # JSON config + PROMPT_ENHANCER_* overrides + validation
  system_prompt.py # the enhancer prompt + per-profile variants
  policy.py        # classify a prompt: enhance / passthrough / raw
  proxy.py         # local replacing proxy (ANTHROPIC_BASE_URL); stats/metrics/tracing
  hook.py          # UserPromptSubmit hook (append)
  launcher.py      # `enhance` — rewrite first prompt, start proxy, launch claude
  cli.py           # `enhance-cli` — rewrite to clipboard, REPL, watch, config/doctor/init/stats
tests/   docs/   packaging/   deploy/   examples/settings.json
```

---

## License

MIT — see [LICENSE](LICENSE).

# Quickstart

## Install

```bash
pip install prompt-preflight        # core (uses your Claude Code CLI auth)
pip install "prompt-preflight[api]" # add the Anthropic SDK for the api backend
```

One-shot, no install:

```bash
uvx prompt-preflight --help         # via uv
pipx run prompt-preflight --help    # via pipx
```

The default backend is **auto**: it uses your existing Claude Code CLI login (no separate
API key required), or an `ANTHROPIC_API_KEY` if one is set, and otherwise falls back to a
dependency-free heuristic cleanup so *something* still happens.

## Verify your setup

```bash
enhance-cli doctor
```

This checks the `claude` binary, the verified flags, your auth, and runs one live
enhancement end-to-end.

## The CLI

```bash
enhance-cli "make my flask api faster"
```

You'll see the rewrite, a short summary of what changed, and an Accept / Edit / Reject
prompt; the chosen text is copied to your clipboard and printed to stdout.

Useful flags:

| Flag | Effect |
|------|--------|
| `-y` | Accept without prompting |
| `-f FILE` | Read the prompt from a file |
| `--profile coding` | Use a tuned rewrite profile |
| `--diff` | Also show a unified diff |
| `--json` | Emit a structured result (scripting) |
| `--explain` | Print why it did/didn't enhance |
| `--repl` | Interactive rewrite loop |
| `--watch` | Rewrite whatever you copy to the clipboard |
| `//raw …` | Bypass enhancement for this prompt |

## Interactive Claude Code (the proxy)

```bash
enhance "refactor the auth module"
```

`enhance` rewrites your first prompt, starts the local proxy, and launches an interactive
`claude` already routed through it — so every subsequent prompt is pre-flighted too. See
[Proxy](proxy.md).

## The hook (zero-setup fallback)

```bash
enhance-cli init      # registers enhance-hook in ~/.claude/settings.json (with backup)
```

The hook injects a clarified restatement as context. It **auto-disables when the proxy is
active**, so the two never double-enhance.

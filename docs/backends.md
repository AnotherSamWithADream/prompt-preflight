# Backends

The engine is backend-abstracted. Set `backend` in config or
`PROMPT_ENHANCER_BACKEND`, or pass it per call.

| Backend | How it works | Needs |
|---------|--------------|-------|
| `auto` *(default)* | API key → `api`; else a `claude` binary → `cli`; else `heuristic` | nothing |
| `cli` | `claude -p` with Haiku, prompt on **stdin**, `--output-format json` | Claude Code CLI |
| `api` | Anthropic SDK (`messages.create`), system-prompt caching | `prompt-preflight[api]` + key |
| `openai` | OpenAI-compatible chat completions | `openai` SDK + key |
| `ollama` | Local `ollama` chat endpoint over stdlib HTTP | a running Ollama |
| `heuristic` | Rule-based whitespace/casing/structure cleanup, no model | nothing |

## CLI backend

Uses your existing Claude Code login — no separate API key. The prompt is passed on
**stdin** (never argv), so it can't leak into process listings and there's no shell
involved. `cli_bare` (**off by default**) adds `--bare` to skip hook/skill/MCP/memory
discovery for a faster start — but on some Claude Code versions `--bare` bypasses the
interactive login (`claude` reports "Not logged in"), so it's opt-in; if an enabled
`--bare` call fails, the engine automatically retries without it.

## API backend

```bash
pip install "prompt-preflight[api]"
export ANTHROPIC_API_KEY=sk-...
enhance-cli --json "..."   # backend auto-selects api when the key is present
```

The large system prompt is sent with `cache_control: ephemeral` to cut cost and latency
on repeated calls. Token usage and cost are surfaced in `EnhanceResult`, `--json`, and the
proxy `/stats`.

### Bedrock / Vertex

Set `api_provider` to `bedrock` or `vertex` (or `PROMPT_ENHANCER_API_PROVIDER`). These use
their own cloud credential chains — no `ANTHROPIC_API_KEY` — and require the matching
Anthropic SDK extra to be installed.

## Offline: Ollama & heuristic

`ollama` gives fully local, zero-cost enhancement for confidential or air-gapped use.
`heuristic` needs nothing at all and is the `auto` fallback when no model is reachable; it
only tidies whitespace, casing, and terminal punctuation, so it can never distort meaning.

## Plugin backends

Third parties can register a backend without editing the engine, via an entry point:

```toml
# in your package's pyproject.toml
[project.entry-points."prompt_preflight.backends"]
mybackend = "my_pkg.module:enhance_fn"
```

`enhance_fn(raw_prompt, cfg, *, start)` must return an `EnhanceResult`. Select it with
`backend = "mybackend"`. A crashing or misbehaving plugin fails open — it can never break
enhancement.

## Profiles

`--profile concise|detailed|coding|research` (or the `profile` config field) selects a
tuned system-prompt variant. `coding` preserves code, identifiers, paths, and error text
exactly; `research` frames the request as a precise analysis question; `concise` prefers
the shortest faithful rewrite.

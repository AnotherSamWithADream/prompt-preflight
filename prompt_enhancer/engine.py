"""Core prompt-enhancement engine.

Rewrites a raw prompt into a clearer one for a stronger downstream model, using one
of two interchangeable backends:

* **cli**  -- the local ``claude -p`` binary (Haiku). Reuses your Claude Code auth.
* **api**  -- the Anthropic Messages API via the ``anthropic`` SDK and an API key.
* **auto** (default) -- ``api`` when an API key is present, otherwise ``cli``.

Design guarantees
-----------------
* **Fail open.** Any error, non-zero exit, timeout, or empty result returns the
  *original* text unchanged.
* **No shell, no argv leak (cli).** ``claude`` is invoked with an argument list, and the
  prompt is passed on **stdin** -- so prompt contents never reach a shell, never appear
  in the process argument list (``ps``/``/proc``), and the result is read from a robust
  ``--output-format json`` envelope rather than scraped from stdout.
* **Recursion guard (cli).** The child gets ``PROMPT_ENHANCER_ACTIVE=1``; and if
  ``ANTHROPIC_BASE_URL`` points at our own proxy it is removed for the child so the
  enhancement call never loops through the proxy.
* **Privacy.** Nothing is logged or written to disk by default.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from prompt_enhancer.config import Config, load_config, points_at_proxy
from prompt_enhancer.system_prompt import ENHANCER_SYSTEM_PROMPT

#: Set in the child environment so a nested ``claude -p`` invocation -- and the
#: UserPromptSubmit hook it would fire -- detect the recursion and pass through.
RECURSION_GUARD_ENV = "PROMPT_ENHANCER_ACTIVE"

#: The ``claude`` executable name. Override with PROMPT_ENHANCER_CLAUDE_BIN.
CLAUDE_BINARY = os.environ.get("PROMPT_ENHANCER_CLAUDE_BIN", "claude")

#: anthropic SDK exception class names we retry once on (transient).
_TRANSIENT_API_ERRORS = frozenset(
    {
        "RateLimitError",
        "InternalServerError",
        "APIConnectionError",
        "APITimeoutError",
        "OverloadedError",
        "APIStatusError",
    }
)


@dataclass(frozen=True)
class EnhanceResult:
    """Outcome of an enhancement attempt. ``text`` is always safe to use downstream:
    the rewritten prompt on success, the unchanged original on any fail-open path.
    ``error`` is a short, non-sensitive reason code (never the prompt contents)."""

    text: str
    enhanced: bool
    original: str
    error: str | None = None
    elapsed: float = 0.0
    backend: str | None = None


# --------------------------------------------------------------------------- #
# claude binary resolution (memoized; Windows npm-shim problem)               #
# --------------------------------------------------------------------------- #

_bin_cache: dict = {}
_api_client_cache: dict = {}


def reset_caches() -> None:
    """Clear memoized binary resolution and API clients (used by tests)."""
    _bin_cache.clear()
    _api_client_cache.clear()


def _resolve_uncached(name: str) -> str:
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    resolved = shutil.which(name)
    if resolved is None:
        return name  # not found -> let subprocess raise -> fail open
    if os.name != "nt" or resolved.lower().endswith(".exe"):
        return resolved
    candidate = os.path.join(
        os.path.dirname(resolved),
        "node_modules",
        "@anthropic-ai",
        "claude-code",
        "bin",
        "claude.exe",
    )
    return candidate if os.path.isfile(candidate) else resolved


def resolve_claude_binary() -> str:
    """Resolve ``claude`` to something ``subprocess`` can launch directly with a list of
    arguments. On Windows the npm shim (``claude.cmd``) is resolved to the bundled
    ``...\\bin\\claude.exe``; on POSIX the resolved shim is directly executable. Result
    is memoized per ``PROMPT_ENHANCER_CLAUDE_BIN`` value."""
    name = os.environ.get("PROMPT_ENHANCER_CLAUDE_BIN", "claude")
    cached = _bin_cache.get(name)
    if cached is not None:
        return cached
    result = _resolve_uncached(name)
    _bin_cache[name] = result
    return result


# Backwards-compatible private alias (referenced by the launcher and tests).
_resolve_claude_binary = resolve_claude_binary


def build_command(
    *,
    model: str = "haiku",
    max_turns: str = "1",
    binary: str | None = None,
    output_format: str = "json",
) -> list:
    """Build the ``claude`` argv. The prompt is NOT here -- it is passed on stdin so it
    never appears in the process argument list. Exposed so tests can assert the flags."""
    return [
        binary or CLAUDE_BINARY,
        "-p",
        "--model",
        model,
        # Full REPLACE, not append: appending leaves Claude Code's coding-assistant
        # identity dominant, which makes Haiku *answer* instead of rewrite.
        "--system-prompt",
        ENHANCER_SYSTEM_PROMPT,
        "--max-turns",
        max_turns,
        # Disable ALL tools (`--tools ""` disables all; `--allowedTools` only controls
        # auto-approval). Single-shot text rewrite.
        "--tools",
        "",
        # Robust structured envelope instead of scraping raw stdout.
        "--output-format",
        output_format,
    ]


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def _select_backend(backend: str, cfg: Config) -> str:
    if backend in ("cli", "api"):
        return backend
    return "api" if os.environ.get(cfg.api_key_env) else "cli"


def enhance(
    raw_prompt: str,
    *,
    backend: str | None = None,
    model: str | None = None,
    api_model: str | None = None,
    timeout: float | None = None,
    max_turns: int | None = None,
    config: Config | None = None,
) -> EnhanceResult:
    """Rewrite ``raw_prompt``, failing open to the original on any problem."""
    original = raw_prompt
    if not raw_prompt or not raw_prompt.strip():
        return EnhanceResult(original, False, original, error="empty-input")

    cfg = config if config is not None else load_config()
    if cfg.max_prompt_chars and len(raw_prompt) > cfg.max_prompt_chars:
        return EnhanceResult(original, False, original, error="too-long")

    chosen = _select_backend(backend or cfg.backend, cfg)
    eff_timeout = cfg.timeout if timeout is None else timeout
    start = time.monotonic()

    if chosen == "api":
        return _run_api(
            raw_prompt, cfg, model=api_model or cfg.api_model, timeout=eff_timeout, start=start
        )
    return _run_cli(
        raw_prompt,
        cfg,
        model=model or cfg.model,
        max_turns=str(cfg.max_turns if max_turns is None else max_turns),
        timeout=eff_timeout,
        start=start,
    )


async def aenhance(raw_prompt: str, **kwargs) -> EnhanceResult:
    """Async wrapper around :func:`enhance` (runs it in a worker thread)."""
    import asyncio

    return await asyncio.to_thread(enhance, raw_prompt, **kwargs)


# --------------------------------------------------------------------------- #
# Backends                                                                     #
# --------------------------------------------------------------------------- #


def _parse_cli_json(stdout: str | None) -> str | None:
    """Extract the rewritten text from a ``--output-format json`` envelope."""
    if not stdout or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("is_error"):
        return None
    result = data.get("result")
    return result if isinstance(result, str) else None


def _run_cli(
    raw_prompt: str, cfg: Config, *, model: str, max_turns: str, timeout: float, start: float
) -> EnhanceResult:
    child_env = dict(os.environ)
    child_env[RECURSION_GUARD_ENV] = "1"
    if points_at_proxy(child_env.get("ANTHROPIC_BASE_URL"), cfg):
        child_env.pop("ANTHROPIC_BASE_URL", None)

    cmd = build_command(model=model, max_turns=max_turns, binary=resolve_claude_binary())
    try:
        proc = subprocess.run(
            cmd,
            input=raw_prompt,  # prompt via stdin: off the command line, injection-proof
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return _fail_open(raw_prompt, "timeout", start, "cli")
    except (OSError, ValueError) as exc:
        return _fail_open(raw_prompt, f"spawn-failed:{type(exc).__name__}", start, "cli")

    if proc.returncode != 0:
        return _fail_open(raw_prompt, f"exit-{proc.returncode}", start, "cli")

    rewritten = _parse_cli_json(proc.stdout)
    if rewritten is None:
        return _fail_open(raw_prompt, "bad-output", start, "cli")
    rewritten = rewritten.strip()
    if not rewritten:
        return _fail_open(raw_prompt, "empty-output", start, "cli")

    elapsed = time.monotonic() - start
    _log(
        {"event": "enhanced", "backend": "cli", "elapsed": round(elapsed, 3)}, raw_prompt, rewritten
    )
    return EnhanceResult(rewritten, True, raw_prompt, elapsed=elapsed, backend="cli")


def _get_client(anthropic, key: str, base_url: str, timeout: float):
    """Reuse one Anthropic client per (key, base_url) for connection pooling."""
    cache_key = (key, base_url)
    client = _api_client_cache.get(cache_key)
    if client is None:
        client = anthropic.Anthropic(api_key=key, base_url=base_url, timeout=timeout)
        _api_client_cache[cache_key] = client
    return client


def _run_api(
    raw_prompt: str, cfg: Config, *, model: str, timeout: float, start: float
) -> EnhanceResult:
    try:
        import anthropic
    except ImportError:
        return _fail_open(raw_prompt, "anthropic-not-installed", start, "api")

    key = os.environ.get(cfg.api_key_env)
    if not key:
        return _fail_open(raw_prompt, "no-api-key", start, "api")

    # Cache the large constant system prompt to cut cost/latency on repeated calls.
    system = [
        {"type": "text", "text": ENHANCER_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    attempts = 1 + max(0, cfg.api_retries)
    last_error = "api-error"

    for attempt in range(attempts):
        try:
            client = _get_client(anthropic, key, cfg.upstream_base, timeout)
            message = client.messages.create(
                model=model,
                max_tokens=cfg.api_max_tokens,
                system=system,
                messages=[{"role": "user", "content": raw_prompt}],
            )
        except Exception as exc:  # noqa: BLE001 -- any failure must fail open
            last_error = f"api-error:{type(exc).__name__}"
            if attempt + 1 < attempts and type(exc).__name__ in _TRANSIENT_API_ERRORS:
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
                continue
            return _fail_open(raw_prompt, last_error, start, "api")

        parts = [
            getattr(b, "text", "")
            for b in getattr(message, "content", [])
            if getattr(b, "type", "") == "text"
        ]
        rewritten = "".join(parts).strip()
        if not rewritten:
            return _fail_open(raw_prompt, "empty-output", start, "api")
        elapsed = time.monotonic() - start
        _log(
            {"event": "enhanced", "backend": "api", "elapsed": round(elapsed, 3)},
            raw_prompt,
            rewritten,
        )
        return EnhanceResult(rewritten, True, raw_prompt, elapsed=elapsed, backend="api")

    return _fail_open(raw_prompt, last_error, start, "api")


# --------------------------------------------------------------------------- #
# Fail-open + opt-in diagnostics                                               #
# --------------------------------------------------------------------------- #


def _fail_open(original: str, reason: str, start: float, backend: str) -> EnhanceResult:
    elapsed = time.monotonic() - start
    _log(
        {"event": "fail-open", "backend": backend, "reason": reason, "elapsed": round(elapsed, 3)},
        original,
        None,
    )
    return EnhanceResult(original, False, original, error=reason, elapsed=elapsed, backend=backend)


def _log(event: dict, original: str, rewritten: str | None) -> None:
    """Opt-in, local-only diagnostics. No-op unless ``PROMPT_ENHANCER_LOG`` names a path.
    Metadata only; prompt CONTENTS are included only when ``PROMPT_ENHANCER_LOG_CONTENT=1``."""
    path = os.environ.get("PROMPT_ENHANCER_LOG")
    if not path:
        return
    record = dict(event)
    record["original_chars"] = len(original)
    if rewritten is not None:
        record["rewritten_chars"] = len(rewritten)
    if os.environ.get("PROMPT_ENHANCER_LOG_CONTENT") == "1":
        record["original"] = original
        record["rewritten"] = rewritten
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass

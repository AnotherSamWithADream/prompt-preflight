"""Core prompt-enhancement engine.

Rewrites a raw prompt into a clearer one for a stronger downstream model, using one of
several interchangeable backends:

* **cli**    -- the local ``claude -p`` binary (Haiku). Reuses your Claude Code auth.
* **api**    -- the Anthropic Messages API via the ``anthropic`` SDK and an API key.
* **openai** -- any OpenAI-compatible Chat Completions endpoint.
* **ollama** -- a local Ollama server (fully offline, zero cost).
* **auto** (default) -- ``api`` when an Anthropic key is present, otherwise ``cli``.

Design guarantees
-----------------
* **Fail open.** Any error, non-zero exit, timeout, empty/implausible/unfaithful result,
  or detected secret returns the *original* text unchanged.
* **No shell, no argv leak (cli).** ``claude`` is invoked with an argument list and the
  prompt is passed on **stdin**, parsed from a ``--output-format json`` envelope.
* **Recursion guard (cli).** The child gets ``PROMPT_ENHANCER_ACTIVE=1``; a proxy
  ``ANTHROPIC_BASE_URL`` is removed for the child so enhancement never loops.
* **Privacy.** Nothing is logged or written to disk by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from prompt_enhancer.config import Config, load_config, points_at_proxy
from prompt_enhancer.safety import (
    clean_output,
    find_pii,
    find_secret,
    missing_tokens,
    plausible_length,
)
from prompt_enhancer.system_prompt import ENHANCER_SYSTEM_PROMPT, system_prompt_for

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
    cost_usd: float | None = None
    usage: dict | None = None


# --------------------------------------------------------------------------- #
# Memoized state (binary resolution, API clients, circuit breaker, results)   #
# --------------------------------------------------------------------------- #

_bin_cache: dict = {}
_api_client_cache: dict = {}
_result_cache: dict = {}
_plugin_cache: dict = {}
_breaker = {"failures": 0, "open_until": 0.0}

#: Backends implemented in-tree. Anything else is looked up as a plugin entry point.
_BUILTIN_BACKENDS = ("cli", "api", "openai", "ollama", "heuristic")
#: Entry-point group third parties register custom backends under.
PLUGIN_GROUP = "prompt_preflight.backends"
_MISSING = object()


def reset_caches() -> None:
    """Clear all memoized state (used by tests)."""
    _bin_cache.clear()
    _api_client_cache.clear()
    _result_cache.clear()
    _plugin_cache.clear()
    system_prompt_for.cache_clear()
    _breaker["failures"] = 0
    _breaker["open_until"] = 0.0


# --------------------------------------------------------------------------- #
# claude binary resolution (Windows npm-shim problem)                         #
# --------------------------------------------------------------------------- #


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
    """Resolve ``claude`` to something ``subprocess`` can launch directly (a real ``.exe``
    on Windows, the shim on POSIX). Memoized per ``PROMPT_ENHANCER_CLAUDE_BIN`` value."""
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
    system: str | None = None,
    bare: bool = False,
) -> list:
    """Build the ``claude`` argv. The prompt is NOT here -- it is passed on stdin so it
    never appears in the process argument list. Exposed so tests can assert the flags."""
    cmd = [binary or CLAUDE_BINARY, "-p"]
    if bare:
        # Skip hook/skill/MCP/memory discovery: faster startup + extra recursion defense.
        cmd.append("--bare")
    cmd += [
        "--model",
        model,
        # Full REPLACE, not append: appending leaves Claude Code's coding-assistant
        # identity dominant, which makes Haiku *answer* instead of rewrite.
        "--system-prompt",
        system if system is not None else ENHANCER_SYSTEM_PROMPT,
        "--max-turns",
        max_turns,
        # Disable ALL tools (`--tools ""` disables all; `--allowedTools` only auto-approves).
        "--tools",
        "",
        # Robust structured envelope instead of scraping raw stdout.
        "--output-format",
        output_format,
    ]
    return cmd


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def _claude_available() -> bool:
    """Whether a ``claude`` executable can be found (without launching it)."""
    name = os.environ.get("PROMPT_ENHANCER_CLAUDE_BIN", "claude")
    if os.path.isabs(name):
        return os.path.isfile(name)
    return shutil.which(name) is not None


def find_plugin_backend(name: str):
    """Return a third-party backend callable registered under :data:`PLUGIN_GROUP`.

    The callable is invoked as ``fn(raw_prompt, cfg, *, start)`` and must return an
    :class:`EnhanceResult`. Returns ``None`` if no such plugin is installed. Result is
    memoized (including misses) so entry-point discovery happens at most once per name.
    """
    cached = _plugin_cache.get(name, _MISSING)
    if cached is not _MISSING:
        return cached
    fn = None
    try:
        from importlib.metadata import entry_points

        try:
            eps = list(entry_points(group=PLUGIN_GROUP))  # py3.10+
        except TypeError:  # pragma: no cover - py3.9 fallback
            eps = list(entry_points().get(PLUGIN_GROUP, []))
        for ep in eps:
            if ep.name == name:
                fn = ep.load()
                break
    except Exception:  # noqa: BLE001 -- a broken plugin must never break enhancement
        fn = None
    _plugin_cache[name] = fn
    return fn


def _select_backend(backend: str, cfg: Config) -> str:
    if backend in _BUILTIN_BACKENDS:
        return backend
    if backend != "auto" and find_plugin_backend(backend) is not None:
        return backend  # an installed third-party backend
    # auto (or an unknown name with no matching plugin): prefer API key, then CLI, then
    # the dependency-free heuristic so enhancement still does *something* with no LLM.
    if os.environ.get(cfg.api_key_env):
        return "api"
    if _claude_available():
        return "cli"
    return "heuristic"


def _model_for(chosen: str, cfg: Config, model: str | None, api_model: str | None) -> str:
    return {
        "cli": model or cfg.model,
        "api": api_model or cfg.api_model,
        "openai": cfg.openai_model,
        "ollama": cfg.ollama_model,
        "heuristic": "heuristic",
    }.get(chosen, cfg.model)


def _breaker_is_open(cfg: Config) -> bool:
    return cfg.circuit_breaker_threshold > 0 and time.monotonic() < _breaker["open_until"]


def _breaker_note(cfg: Config, ok: bool) -> None:
    if cfg.circuit_breaker_threshold <= 0:
        return
    if ok:
        _breaker["failures"] = 0
    else:
        _breaker["failures"] += 1
        if _breaker["failures"] >= cfg.circuit_breaker_threshold:
            _breaker["open_until"] = time.monotonic() + cfg.circuit_breaker_cooldown
            _breaker["failures"] = 0


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
    if not cfg.enabled or os.environ.get("PROMPT_ENHANCER_DISABLE"):
        return EnhanceResult(original, False, original, error="disabled")
    if cfg.max_prompt_chars and len(raw_prompt) > cfg.max_prompt_chars:
        return EnhanceResult(original, False, original, error="too-long")
    if cfg.redact_secrets:
        secret = find_secret(raw_prompt)
        if secret:
            # Never send a prompt that carries a credential to the enhancer; the original
            # (with the secret the user intended) still proceeds to the strong model.
            return EnhanceResult(original, False, original, error=f"secret-detected:{secret}")
    if cfg.warn_pii:
        pii = find_pii(raw_prompt)
        if pii:
            sys.stderr.write(f"prompt-enhancer: warning: prompt may contain {pii}\n")

    chosen = _select_backend(backend or cfg.backend, cfg)
    eff_timeout = cfg.timeout if timeout is None else timeout

    key = None
    if cfg.cache_results:
        key = _cache_key(raw_prompt, chosen, _model_for(chosen, cfg, model, api_model), cfg.profile)
        cached = _result_cache.get(key)
        if cached is not None:
            return cached

    if _breaker_is_open(cfg):
        return EnhanceResult(original, False, original, error="circuit-open", backend=chosen)

    start = time.monotonic()
    if chosen == "api":
        result = _run_api(
            raw_prompt, cfg, model=api_model or cfg.api_model, timeout=eff_timeout, start=start
        )
    elif chosen == "openai":
        result = _run_openai(raw_prompt, cfg, timeout=eff_timeout, start=start)
    elif chosen == "ollama":
        result = _run_ollama(raw_prompt, cfg, timeout=eff_timeout, start=start)
    elif chosen == "heuristic":
        result = _run_heuristic(raw_prompt, cfg, start=start)
    elif chosen == "cli":
        result = _run_cli(
            raw_prompt,
            cfg,
            model=model or cfg.model,
            max_turns=str(cfg.max_turns if max_turns is None else max_turns),
            timeout=eff_timeout,
            start=start,
        )
    else:
        result = _run_plugin(chosen, raw_prompt, cfg, start=start)

    if result.enhanced:
        result = _postprocess(original, result, cfg, start)

    _breaker_note(cfg, result.enhanced)
    if result.enhanced and key is not None:
        _result_cache[key] = result
    return result


async def aenhance(raw_prompt: str, **kwargs) -> EnhanceResult:
    """Async wrapper around :func:`enhance` (runs it in a worker thread)."""
    import asyncio

    return await asyncio.to_thread(enhance, raw_prompt, **kwargs)


def _cache_key(raw: str, backend: str, model: str, profile: str) -> str:
    return hashlib.sha256(f"{backend}|{model}|{profile}|{raw}".encode()).hexdigest()


def _postprocess(original: str, result: EnhanceResult, cfg: Config, start: float) -> EnhanceResult:
    """Apply output cleanup + faithfulness/length guards. Fails open on violation."""
    text = clean_output(result.text) if cfg.clean_output else result.text
    if not text.strip():
        return _fail_open(original, "empty-after-clean", start, result.backend or "?")
    if cfg.faithfulness_check and missing_tokens(original, text):
        return _fail_open(original, "faithfulness", start, result.backend or "?")
    if not plausible_length(original, text, cfg.length_ratio_min, cfg.length_ratio_max):
        return _fail_open(original, "implausible-length", start, result.backend or "?")
    if text == result.text:
        return result
    return EnhanceResult(
        text,
        True,
        original,
        elapsed=result.elapsed,
        backend=result.backend,
        cost_usd=result.cost_usd,
        usage=result.usage,
    )


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


def _slim_usage(usage) -> dict | None:
    if not isinstance(usage, dict):
        return None
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    slim = {k: usage[k] for k in keys if isinstance(usage.get(k), int)}
    return slim or None


def _parse_cli_meta(stdout: str | None):
    """Return ``(cost_usd, usage)`` from the CLI JSON envelope, best-effort."""
    try:
        data = json.loads(stdout or "")
    except (ValueError, TypeError):
        return (None, None)
    if not isinstance(data, dict):
        return (None, None)
    cost = data.get("total_cost_usd")
    return (cost if isinstance(cost, (int, float)) else None, _slim_usage(data.get("usage")))


def _run_cli(
    raw_prompt: str, cfg: Config, *, model: str, max_turns: str, timeout: float, start: float
) -> EnhanceResult:
    child_env = dict(os.environ)
    child_env[RECURSION_GUARD_ENV] = "1"
    if points_at_proxy(child_env.get("ANTHROPIC_BASE_URL"), cfg):
        child_env.pop("ANTHROPIC_BASE_URL", None)

    cmd = build_command(
        model=model,
        max_turns=max_turns,
        binary=resolve_claude_binary(),
        system=system_prompt_for(cfg.profile),
        bare=cfg.cli_bare,
    )
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

    cost, usage = _parse_cli_meta(proc.stdout)
    elapsed = time.monotonic() - start
    _log(
        {"event": "enhanced", "backend": "cli", "elapsed": round(elapsed, 3)}, raw_prompt, rewritten
    )
    return EnhanceResult(
        rewritten, True, raw_prompt, elapsed=elapsed, backend="cli", cost_usd=cost, usage=usage
    )


def _get_client(anthropic, cfg: Config, key: str | None, timeout: float):
    """Reuse one Anthropic client per (provider, key, base_url) for connection pooling.

    ``api_provider`` selects the SDK client: the default direct API, AWS Bedrock, or GCP
    Vertex. Bedrock/Vertex authenticate via their own cloud credential chains (no
    ``ANTHROPIC_API_KEY``) and require the matching extra to be installed.
    """
    provider = cfg.api_provider
    cache_key = (provider, key, cfg.upstream_base)
    client = _api_client_cache.get(cache_key)
    if client is None:
        if provider == "bedrock":
            client = anthropic.AnthropicBedrock(timeout=timeout)
        elif provider == "vertex":
            client = anthropic.AnthropicVertex(timeout=timeout)
        else:
            client = anthropic.Anthropic(api_key=key, base_url=cfg.upstream_base, timeout=timeout)
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
    if not key and cfg.api_provider == "anthropic":
        # Bedrock/Vertex use their own cloud credential chains, so a missing
        # ANTHROPIC_API_KEY is only fatal for the direct API.
        return _fail_open(raw_prompt, "no-api-key", start, "api")

    # Cache the large constant system prompt to cut cost/latency on repeated calls.
    system = [
        {
            "type": "text",
            "text": system_prompt_for(cfg.profile),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    attempts = 1 + max(0, cfg.api_retries)
    last_error = "api-error"

    for attempt in range(attempts):
        try:
            client = _get_client(anthropic, cfg, key, timeout)
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
        usage = getattr(message, "usage", None)
        usage_d = _slim_usage(
            {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            }
            if usage is not None
            else None
        )
        elapsed = time.monotonic() - start
        _log(
            {"event": "enhanced", "backend": "api", "elapsed": round(elapsed, 3)},
            raw_prompt,
            rewritten,
        )
        return EnhanceResult(
            rewritten, True, raw_prompt, elapsed=elapsed, backend="api", usage=usage_d
        )

    return _fail_open(raw_prompt, last_error, start, "api")


def _run_openai(raw_prompt: str, cfg: Config, *, timeout: float, start: float) -> EnhanceResult:
    try:
        import openai
    except ImportError:
        return _fail_open(raw_prompt, "openai-not-installed", start, "openai")
    key = os.environ.get(cfg.openai_key_env)
    if not key:
        return _fail_open(raw_prompt, "no-api-key", start, "openai")
    try:
        kwargs = {"api_key": key}
        if cfg.openai_base_url:
            kwargs["base_url"] = cfg.openai_base_url
        client = openai.OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=cfg.openai_model,
            max_tokens=cfg.api_max_tokens,
            timeout=timeout,
            messages=[
                {"role": "system", "content": system_prompt_for(cfg.profile)},
                {"role": "user", "content": raw_prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _fail_open(raw_prompt, f"openai-error:{type(exc).__name__}", start, "openai")
    if not text:
        return _fail_open(raw_prompt, "empty-output", start, "openai")
    elapsed = time.monotonic() - start
    _log({"event": "enhanced", "backend": "openai", "elapsed": round(elapsed, 3)}, raw_prompt, text)
    return EnhanceResult(text, True, raw_prompt, elapsed=elapsed, backend="openai")


def _run_ollama(raw_prompt: str, cfg: Config, *, timeout: float, start: float) -> EnhanceResult:
    import urllib.error
    import urllib.request

    body = json.dumps(
        {
            "model": cfg.ollama_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt_for(cfg.profile)},
                {"role": "user", "content": raw_prompt},
            ],
        }
    ).encode("utf-8")
    url = cfg.ollama_base_url.rstrip("/") + "/api/chat"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- local
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _fail_open(raw_prompt, f"ollama-error:{type(exc).__name__}", start, "ollama")
    text = (
        ((data.get("message") or {}).get("content") or "").strip() if isinstance(data, dict) else ""
    )
    if not text:
        return _fail_open(raw_prompt, "empty-output", start, "ollama")
    elapsed = time.monotonic() - start
    _log({"event": "enhanced", "backend": "ollama", "elapsed": round(elapsed, 3)}, raw_prompt, text)
    return EnhanceResult(text, True, raw_prompt, elapsed=elapsed, backend="ollama")


def _run_heuristic(raw_prompt: str, cfg: Config, *, start: float) -> EnhanceResult:
    """Dependency-free, no-LLM normaliser. Always available; the ``auto`` fallback when
    neither a CLI binary nor an API key is present. Conservative by design — it only
    tidies whitespace, capitalises the first letter, and adds terminal punctuation, so it
    can never distort meaning or fail the faithfulness guard."""
    text = re.sub(r"[ \t]+", " ", raw_prompt.strip())
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Capitalise the first letter of the first alphabetic word -- but skip a leading literal
    # token (URL/path/code), where capitalising would corrupt a hard specific (e.g.
    # "https://x" -> "Https://x", "`cfg`" -> "`Cfg`"). Because the faithfulness check is
    # case-insensitive it would not even catch such corruption.
    for m in re.finditer(r"\S+", text):
        tok = m.group()
        if not any(c.isalpha() for c in tok):
            continue  # e.g. a leading "123" or "--": look at the next token
        literal = "://" in tok or "/" in tok or "\\" in tok or "`" in tok or tok[:1] in "~.-"
        if not literal:
            j = next(k for k in range(m.start(), m.end()) if text[k].isalpha())
            text = text[:j] + text[j].upper() + text[j + 1 :]
        break  # only the first alphabetic word is considered
    if text and text[-1] not in ".!?:`)]\"'" and "\n" not in text:
        text += "."
    elapsed = time.monotonic() - start
    _log(
        {"event": "enhanced", "backend": "heuristic", "elapsed": round(elapsed, 3)},
        raw_prompt,
        text,
    )
    return EnhanceResult(text, True, raw_prompt, elapsed=elapsed, backend="heuristic")


def _run_plugin(name: str, raw_prompt: str, cfg: Config, *, start: float) -> EnhanceResult:
    """Dispatch to a third-party backend registered under :data:`PLUGIN_GROUP`."""
    fn = find_plugin_backend(name)
    if fn is None:
        return _fail_open(raw_prompt, f"unknown-backend:{name}", start, name)
    try:
        result = fn(raw_prompt, cfg, start=start)
    except Exception as exc:  # noqa: BLE001 -- a plugin must never break enhancement
        return _fail_open(raw_prompt, f"plugin-error:{type(exc).__name__}", start, name)
    if not isinstance(result, EnhanceResult):
        return _fail_open(raw_prompt, "plugin-bad-result", start, name)
    return result


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

"""Configuration for prompt-enhancer.

One easy-to-edit JSON file plus environment-variable overrides. Resolution order
(lowest to highest priority):

1. built-in defaults (this file)
2. a JSON config file
3. ``PROMPT_ENHANCER_*`` environment variables

The config file is looked up in this order:

1. ``$PROMPT_ENHANCER_CONFIG`` (explicit path)
2. ``./.prompt-enhancer.json`` (project-local)
3. the per-user path: ``%APPDATA%\\prompt-enhancer\\config.json`` on Windows, or
   ``$XDG_CONFIG_HOME/prompt-enhancer/config.json`` (default ``~/.config/...``) elsewhere

Manage it with ``enhance-cli config`` (see the CLI).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from urllib.parse import urlsplit


@dataclass
class Config:
    # --- behaviour ---------------------------------------------------------
    enabled: bool = True
    backend: str = "auto"  # "auto" | "cli" | "api"
    word_threshold: int = 12  # prompts shorter than this are passed through
    bypass_prefix: str = "//raw"  # skip-enhancement marker
    max_prompt_chars: int = (
        100_000  # skip enhancement above this (cost/context guard); 0 = no limit
    )

    # --- CLI backend (claude -p, reuses Claude Code auth, no API key) -------
    model: str = "haiku"  # --model alias/name
    max_turns: int = 1
    timeout: float = 15.0  # seconds before fail-open

    # --- API backend (Anthropic SDK, needs an API key) ---------------------
    api_model: str = "claude-haiku-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    api_max_tokens: int = 2048
    api_retries: int = 1  # retries on transient API errors (429/5xx/overloaded)

    # --- proxy (true prompt replacement for interactive Claude Code) -------
    upstream_base: str = "https://api.anthropic.com"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8788
    proxy_connect_timeout: float = 10.0  # connect to upstream
    proxy_upstream_timeout: float = 600.0  # stream a (possibly long) response
    proxy_max_body_bytes: int = 16_000_000  # reject oversized request bodies
    proxy_max_concurrency: int = 4  # max simultaneous enhancement calls
    allow_public_bind: bool = False  # refuse non-loopback proxy_host unless True
    # Text blocks containing this marker are treated as injected context, not the human
    # prompt (Claude Code wraps context in <system-reminder>...</system-reminder>).
    proxy_reminder_marker: str = "<system-reminder"
    # Requests whose target model contains any of these substrings are NOT
    # enhanced (skips Claude Code's background/title calls, which use Haiku, and
    # avoids enhancing prompts already aimed at the small model).
    proxy_skip_models: tuple = ("haiku",)
    # Only rewrite requests that carry a tool list -- the main agentic turn does;
    # background/title/utility calls do not. Distinguishes them reliably.
    proxy_require_tools: bool = True

    # Note: opt-in, local-only diagnostics are controlled by the environment
    # variables PROMPT_ENHANCER_LOG (path) and PROMPT_ENHANCER_LOG_CONTENT=1,
    # not by this file -- so prompt contents are never written to disk by default.


_BOOL_TRUE = {"1", "true", "yes", "on"}


def _coerce(name: str, value, default):
    """Coerce a string (from env/JSON) to the type of the dataclass default."""
    if isinstance(default, bool):
        return str(value).strip().lower() in _BOOL_TRUE if isinstance(value, str) else bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, tuple):
        if isinstance(value, str):
            return tuple(s.strip() for s in value.split(",") if s.strip())
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return default
    return value  # str / None


# Environment variable -> Config field.
_ENV_MAP = {
    "PROMPT_ENHANCER_ENABLED": "enabled",
    "PROMPT_ENHANCER_BACKEND": "backend",
    "PROMPT_ENHANCER_WORD_THRESHOLD": "word_threshold",
    "PROMPT_ENHANCER_BYPASS_PREFIX": "bypass_prefix",
    "PROMPT_ENHANCER_MAX_PROMPT_CHARS": "max_prompt_chars",
    "PROMPT_ENHANCER_MODEL": "model",
    "PROMPT_ENHANCER_MAX_TURNS": "max_turns",
    "PROMPT_ENHANCER_TIMEOUT": "timeout",
    "PROMPT_ENHANCER_API_MODEL": "api_model",
    "PROMPT_ENHANCER_API_KEY_ENV": "api_key_env",
    "PROMPT_ENHANCER_API_MAX_TOKENS": "api_max_tokens",
    "PROMPT_ENHANCER_API_RETRIES": "api_retries",
    "PROMPT_ENHANCER_UPSTREAM_BASE": "upstream_base",
    "PROMPT_ENHANCER_PROXY_HOST": "proxy_host",
    "PROMPT_ENHANCER_PROXY_PORT": "proxy_port",
    "PROMPT_ENHANCER_PROXY_CONNECT_TIMEOUT": "proxy_connect_timeout",
    "PROMPT_ENHANCER_PROXY_UPSTREAM_TIMEOUT": "proxy_upstream_timeout",
    "PROMPT_ENHANCER_PROXY_MAX_BODY_BYTES": "proxy_max_body_bytes",
    "PROMPT_ENHANCER_PROXY_MAX_CONCURRENCY": "proxy_max_concurrency",
    "PROMPT_ENHANCER_ALLOW_PUBLIC_BIND": "allow_public_bind",
    "PROMPT_ENHANCER_PROXY_REMINDER_MARKER": "proxy_reminder_marker",
    "PROMPT_ENHANCER_PROXY_SKIP_MODELS": "proxy_skip_models",
    "PROMPT_ENHANCER_PROXY_REQUIRE_TOOLS": "proxy_require_tools",
}

#: Allowed values for the ``backend`` field.
_VALID_BACKENDS = ("auto", "cli", "api")


class ConfigError(ValueError):
    """Raised for an invalid configuration value."""


def validate(cfg: Config) -> list:
    """Return a list of human-readable problems with ``cfg`` (empty == valid)."""
    problems = []
    if cfg.backend not in _VALID_BACKENDS:
        problems.append(f"backend must be one of {_VALID_BACKENDS!r}, got {cfg.backend!r}")
    if not (0 < cfg.proxy_port < 65536):
        problems.append(f"proxy_port must be 1-65535, got {cfg.proxy_port}")
    if cfg.word_threshold < 0:
        problems.append(f"word_threshold must be >= 0, got {cfg.word_threshold}")
    if cfg.timeout <= 0:
        problems.append(f"timeout must be > 0, got {cfg.timeout}")
    if cfg.api_retries < 0:
        problems.append(f"api_retries must be >= 0, got {cfg.api_retries}")
    return problems


def user_config_path() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "prompt-enhancer", "config.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "prompt-enhancer", "config.json")


def config_path() -> str:
    """The active config file path (may not exist yet)."""
    explicit = os.environ.get("PROMPT_ENHANCER_CONFIG")
    if explicit:
        return explicit
    local = os.path.join(os.getcwd(), ".prompt-enhancer.json")
    if os.path.isfile(local):
        return local
    return user_config_path()


def _field_names() -> set:
    return {f.name for f in fields(Config)}


def load_config() -> Config:
    cfg = Config()
    names = _field_names()

    # 2. JSON file
    path = config_path()
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in names:
                        setattr(
                            cfg, key, _coerce(key, value, getattr(Config, key, getattr(cfg, key)))
                        )
        except (OSError, ValueError):
            pass  # malformed config never breaks enhancement -> defaults stand

    # 3. environment overrides
    for env_name, field_name in _ENV_MAP.items():
        if env_name in os.environ:
            setattr(
                cfg, field_name, _coerce(field_name, os.environ[env_name], getattr(cfg, field_name))
            )

    # Surface invalid values instead of silently falling back (never raise: the engine
    # must stay fail-open). `enhance-cli doctor` / `config show` report these too.
    problems = validate(cfg)
    if problems and os.environ.get("PROMPT_ENHANCER_QUIET_CONFIG") != "1":
        import sys

        for p in problems:
            sys.stderr.write(f"prompt-enhancer: config warning: {p}\n")

    return cfg


def to_dict(cfg: Config) -> dict:
    d = asdict(cfg)
    d["proxy_skip_models"] = list(cfg.proxy_skip_models)  # JSON has no tuples
    return d


def points_at_proxy(base_url: str | None, cfg: Config) -> bool:
    """True if ``base_url`` (e.g. an ``ANTHROPIC_BASE_URL``) points at our local proxy.

    Used by the engine (to avoid the enhancement call looping back through the proxy)
    and by the hook (to step aside when the proxy is handling enhancement).
    """
    if not base_url:
        return False
    try:
        parts = urlsplit(base_url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    local = {cfg.proxy_host.lower(), "127.0.0.1", "localhost", "0.0.0.0"}
    return host in local and (parts.port or 0) == cfg.proxy_port


def is_local_proxy(base_url: str | None, cfg: Config) -> bool:
    """True if ``base_url`` points at our proxy OR any loopback address.

    Broader than :func:`points_at_proxy`: the hook uses this to step aside whenever
    Claude Code is routed through a local enhancing proxy, even one started on a
    non-default port (so it never double-enhances)."""
    if points_at_proxy(base_url, cfg):
        return True
    if not base_url:
        return False
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def write_template(path: str) -> str:
    """Write a default config file to ``path`` (creating parent dirs). Returns path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(Config()), fh, indent=2)
        fh.write("\n")
    return path

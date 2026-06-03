"""Shared decision logic: should a given prompt be enhanced, skipped, or treated as
``//raw``?

Used by the hook, the CLI, and the proxy so they all behave identically. Pure and
side-effect-free for easy testing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    #: "enhance"      -> run the engine on ``text``
    #: "passthrough"  -> leave the prompt alone (slash command / too short / empty / disabled)
    #: "raw"          -> user opted out with the bypass prefix; ``text`` is the prompt
    #:                   with the prefix stripped (callers that own their output strip it;
    #:                   the hook simply passes through)
    action: str
    text: str


def _is_raw(text: str, prefix: str) -> bool:
    if text == prefix:
        return True
    return any(text.startswith(prefix + sep) for sep in (" ", "\t", "\n", "\r"))


def strip_raw(text: str, prefix: str) -> str:
    """Remove a leading bypass prefix (and following whitespace) from ``text``."""
    lead = text.lstrip()
    if lead == prefix:
        return ""
    for sep in (" ", "\t", "\n", "\r"):
        if lead.startswith(prefix + sep):
            return lead[len(prefix) :].lstrip()
    return text


def classify_prompt(prompt: str, cfg) -> Decision:
    """Decide what to do with a freshly-submitted ``prompt`` given ``cfg``."""
    if not cfg.enabled:
        return Decision("passthrough", prompt)

    stripped = prompt.strip()
    if not stripped:
        return Decision("passthrough", prompt)

    if _is_raw(stripped, cfg.bypass_prefix):
        return Decision("raw", strip_raw(stripped, cfg.bypass_prefix))

    # Slash commands run as-is.
    if stripped.startswith("/"):
        return Decision("passthrough", prompt)

    # Short, already-imperative prompts: err toward not over-rewriting.
    if len(stripped.split()) < cfg.word_threshold:
        return Decision("passthrough", prompt)

    return Decision("enhance", prompt)

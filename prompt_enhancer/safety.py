"""Output-quality and safety helpers for the engine.

All functions are pure and side-effect-free (no logging, no network) so the engine can
use them on the fail-open path without risk.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Secret / PII detection                                                      #
# --------------------------------------------------------------------------- #

_SECRET_PATTERNS = [
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("google api key", re.compile(r"\bAIza[A-Za-z0-9_-]{20,}")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}")),
]

_PII_PATTERNS = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit-card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]


def find_secret(text: str) -> str | None:
    """Return a short label for the first secret-looking pattern, else None."""
    for label, pat in _SECRET_PATTERNS:
        if pat.search(text):
            return label
    return None


def find_pii(text: str) -> str | None:
    for label, pat in _PII_PATTERNS:
        if pat.search(text):
            return label
    return None


# --------------------------------------------------------------------------- #
# Faithfulness: hard specifics that a rewrite must preserve verbatim          #
# --------------------------------------------------------------------------- #

_URL = re.compile(r"https?://[^\s)>\]]+")
_BACKTICK = re.compile(r"`([^`\n]{1,200})`")
_FILE = re.compile(
    r"\b[\w./\\-]*\.(?:py|js|ts|tsx|jsx|java|go|rs|c|cpp|cc|h|hpp|json|ya?ml|toml|md|txt|"
    r"csv|tsv|sql|sh|ps1|rb|php|html|css|xml|cfg|ini|env|lock|cs|kt|swift)\b"
)
_PATH = re.compile(r"\b[\w.-]+[/\\][\w./\\-]+")
_BIGNUM = re.compile(r"\b\d{4,}\b")


def important_tokens(text: str) -> set:
    """Hard specifics a faithful rewrite must keep verbatim: URLs, backtick-quoted spans,
    filenames/paths, and 4+ digit numbers."""
    tokens: set = set()
    tokens.update(_URL.findall(text))
    tokens.update(m.strip() for m in _BACKTICK.findall(text))
    tokens.update(_FILE.findall(text))
    tokens.update(_PATH.findall(text))
    tokens.update(_BIGNUM.findall(text))
    return {t for t in tokens if t and len(t) >= 2}


def missing_tokens(original: str, rewrite: str) -> list:
    """Hard tokens present in ``original`` but absent from ``rewrite`` (case-insensitive)."""
    low = rewrite.lower()
    return [t for t in important_tokens(original) if t.lower() not in low]


# --------------------------------------------------------------------------- #
# Output cleanup + length plausibility                                        #
# --------------------------------------------------------------------------- #

_PREAMBLE = re.compile(
    r"(?is)^\s*(?:sure|okay|certainly|here(?:'s| is| are)|the rewritten prompt is)"
    r"[^\n:]{0,80}:\s*\n"
)


def clean_output(text: str) -> str:
    """Defensively strip wrapping code fences, a leading preamble line, and wrapping quotes
    that a model may add despite the system prompt."""
    t = text.strip()

    if t.startswith("```") and t.rstrip().endswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2:
            t = "\n".join(lines[1:-1]).strip()

    m = _PREAMBLE.match(t)
    if m and len(t) - m.end() > 0:
        t = t[m.end() :].strip()

    if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0] and t.count(t[0]) == 2:
        t = t[1:-1].strip()

    return t


def plausible_length(original: str, rewrite: str, lo: float, hi: float) -> bool:
    """True if the rewrite's length is within [lo, hi] x the original (guards against a
    model that returned almost nothing or a runaway expansion)."""
    o = len(original.strip())
    if o == 0:
        return True
    ratio = len(rewrite.strip()) / o
    return lo <= ratio <= hi

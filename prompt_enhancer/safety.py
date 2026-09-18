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
# Only clearly path-like tokens (rooted, relative, or drive-qualified) count as hard
# specifics. Ordinary prose with a slash -- "input/output", "TCP/IP", "and/or", "12/25/2024"
# -- must NOT be treated as a must-keep-verbatim token, or the default-on faithfulness check
# would silently reject good rewrites of common prompts. Extension-bearing files are covered
# by _FILE above.
_PATH = re.compile(r"(?<!\w)(?:\.{0,2}/|~/|[A-Za-z]:[\\/])[\w./\\-]+")
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


#: Digit-group separators a faithful rewrite may legitimately introduce ("10000" -> "10,000").
_NUM_SEP = re.compile(r"(?<=\d)[,_ ](?=\d)")


def missing_tokens(original: str, rewrite: str) -> list:
    """Hard tokens present in ``original`` but absent from ``rewrite`` (case-insensitive).

    Numeric tokens are also matched against a separator-stripped copy of the rewrite: a
    model that renders ``10000 rps`` as ``10,000 RPS`` has preserved the number exactly,
    and must not be failed for formatting it.
    """
    low = rewrite.lower()
    ungrouped = _NUM_SEP.sub("", low)
    missing = []
    for token in important_tokens(original):
        low_token = token.lower()
        if low_token in low:
            continue
        if token.isdigit() and low_token in ungrouped:
            continue
        missing.append(token)
    return missing


# --------------------------------------------------------------------------- #
# Output cleanup + length plausibility                                        #
# --------------------------------------------------------------------------- #

# A leading meta-preamble the model sometimes adds despite instructions, e.g.
# "Here is the rewritten prompt:" or "Sure, here's the improved version:". We strip it ONLY
# when it explicitly references the rewrite artifact (prompt/version/rewrite/...), so a
# genuine first line of content like "Here are the requirements:" is preserved.
_PREAMBLE = re.compile(
    r"(?is)^\s*(?:(?:sure|okay|certainly|of course)[,!. ]+)?(?:"
    r"here(?:'s| is| are)[^\n:]*?\b(?:prompt|version|rewrite|rewording|request|wording)\b[^\n:]{0,40}"
    r"|(?:the\s+)?(?:rewritten|improved|revised|clarified|enhanced|refined)\s+(?:prompt|version)"
    r"[^\n:]{0,40}"
    r"):\s*\n"
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


# A clarified rewrite of a *very short*, vague prompt is legitimately many times longer
# than the input (e.g. a 3-word prompt gains an "Open questions" section). So the upper
# bound is max(hi*len, this floor): only an expansion that ALSO exceeds this absolute size
# counts as a runaway. Without it, the length guard fails open on short prompts -- the very
# ones that benefit most from enhancement.
_LEN_FLOOR_CHARS = 600


def plausible_length(original: str, rewrite: str, lo: float, hi: float) -> bool:
    """True if the rewrite's length is plausible: at least ``lo``x the original, and no more
    than ``max(hi*len(original), 600)`` (so short prompts may expand freely up to the floor).
    Guards against a model that returned almost nothing or ran away into an essay."""
    o = len(original.strip())
    if o == 0:
        return True
    r = len(rewrite.strip())
    return lo * o <= r <= max(hi * o, _LEN_FLOOR_CHARS)


# --------------------------------------------------------------------------- #
# Prompt-injection hardening                                                  #
# --------------------------------------------------------------------------- #

# The rewrite is model output that gets injected into a *stronger, agentic* model's
# context. A rewrite must never introduce instruction-override text the user did not
# write. Each pattern only trips when it appears in the rewrite but NOT the original,
# so a user who legitimately wrote "ignore the previous approach" is unaffected.
_INJECTION_PATTERNS = [
    (
        "override",
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget)\b[^.\n]{0,40}"
            r"\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,30}"
            r"\b(?:instruction|prompt|rule|direction|context)"
        ),
    ),
    ("role-marker", re.compile(r"(?im)^\s*(?:system|assistant)\s*:")),
    ("persona-switch", re.compile(r"(?i)\byou are now\b|\bfrom now on,? you\b")),
    ("new-instructions", re.compile(r"(?i)\bnew instructions?\s*:")),
    ("tag-injection", re.compile(r"(?i)</?(?:system|system-reminder|instructions)\b")),
]

_URL_HOST = re.compile(r"(?i)https?://([^/\s)>\]]+)")


def _domains(text: str) -> set:
    out = set()
    for host in _URL_HOST.findall(text):
        h = host.lower()
        # NB: prefix strip, not lstrip() -- lstrip("www.") would eat leading w/. characters
        # and turn "wonderful.com" into "onderful.com".
        if h.startswith("www."):
            h = h[4:]
        out.add(h)
    return out


def injection_risk(original: str, rewrite: str) -> str | None:
    """Return a short label if the REWRITE introduces instruction-injection-looking
    content (or a domain) absent from the original, else None.

    Compared against the original so the guard only fires on text the model *added*.
    """
    for label, pat in _INJECTION_PATTERNS:
        if pat.search(rewrite) and not pat.search(original):
            return label
    if _domains(rewrite) - _domains(original):
        return "new-domain"
    return None


# --------------------------------------------------------------------------- #
# "Already well-formed" detection -- don't pay for a no-op rewrite            #
# --------------------------------------------------------------------------- #

_LIST_LINE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S")
#: Hand-wavy words whose presence means the prompt still has something to clarify.
_VAGUE_WORDS = frozenset(
    {
        "something",
        "stuff",
        "somehow",
        "better",
        "nicer",
        "cleaner",
        "faster",
        "good",
        "nice",
        "etc",
        "whatever",
        "anything",
        "properly",
        "correctly",
        "improve",
        "optimize",
    }
)


def looks_well_formed(text: str, min_words: int = 25, max_vague_ratio: float = 0.04) -> bool:
    """True when a prompt is already long, structured/specific and not hand-wavy -- so
    enhancement would add little. Deliberately conservative: when in doubt, return False
    and let the rewriter run (a missed skip only costs a little; a wrong skip loses the
    whole benefit of the tool)."""
    stripped = text.strip()
    words = stripped.split()
    if len(words) < min_words:
        return False
    vague = sum(1 for w in words if w.strip(".,;:!?\"'()").lower() in _VAGUE_WORDS)
    if vague / len(words) >= max_vague_ratio:
        return False
    structured = bool(_LIST_LINE.search(stripped))
    specific = bool(important_tokens(stripped))
    return structured or specific

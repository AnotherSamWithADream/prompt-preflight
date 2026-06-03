"""Claude Code ``UserPromptSubmit`` hook -- the fallback path.

Runs the enhancement engine on your submitted prompt and injects the result as
``additionalContext`` (the platform has no prompt-replacement field for this hook --
see the README). Prefer the proxy for true replacement; this hook is the zero-setup
fallback and **auto-disables when the proxy is the active base URL**, so the two never
double-enhance.

Run as the ``enhance-hook`` console script, or directly (``python path/to/hook.py``).
"""

from __future__ import annotations

import json
import os
import sys

# Allow running this file directly without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_enhancer.config import is_local_proxy, load_config  # noqa: E402
from prompt_enhancer.engine import RECURSION_GUARD_ENV, enhance  # noqa: E402
from prompt_enhancer.policy import classify_prompt  # noqa: E402


def format_clarified_restatement(enhanced: str) -> str:
    """Wrap the enhanced prompt in a clearly delimited, labelled block."""
    return (
        "[prompt pre-flight] Clarified restatement of the user's request\n"
        "The text below is an automatically clarified version of the user's prompt, "
        "produced by a local prompt-enhancer. Treat it as the user's intended request. "
        "If anything in it conflicts with a specific detail, wording, or constraint in "
        "the user's original message above, the ORIGINAL message wins.\n"
        "--- begin clarified restatement ---\n"
        f"{enhanced}\n"
        "--- end clarified restatement ---"
    )


def decide(prompt: str, cfg=None):
    """Pure decision logic. Returns the ``additionalContext`` string to emit, or
    ``None`` to pass the prompt through unchanged."""
    cfg = cfg if cfg is not None else load_config()
    decision = classify_prompt(prompt, cfg)
    if decision.action != "enhance":
        # "raw" also lands here: the hook can't physically strip the token (no
        # prompt-replacement field), so it simply leaves the prompt untouched.
        return None
    result = enhance(prompt, config=cfg)
    if not result.enhanced:
        return None  # fail open
    return format_clarified_restatement(result.text)


def _emit_context(context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )


def main() -> int:
    # Recursion guard FIRST: if our own engine spawned the claude that fired this hook.
    if os.environ.get(RECURSION_GUARD_ENV):
        return 0

    cfg = load_config()
    if not cfg.enabled:
        return 0

    # The proxy does true replacement; if Claude Code is routed through a local proxy
    # (ours, even on a non-default port), step aside so we never double-enhance.
    if is_local_proxy(os.environ.get("ANTHROPIC_BASE_URL"), cfg):
        return 0

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return 0  # malformed input -> pass through
    if not isinstance(data, dict):
        return 0
    # Skip enhancement in plan mode (and other non-default modes are honored as-is).
    if data.get("permission_mode") == "plan":
        return 0
    prompt = data.get("prompt", "")
    if not isinstance(prompt, str):
        return 0

    context = decide(prompt, cfg)
    if context:
        _emit_context(context)
    return 0


if __name__ == "__main__":
    sys.exit(main())

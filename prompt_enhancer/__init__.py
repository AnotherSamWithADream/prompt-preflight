"""prompt_enhancer -- a "prompt pre-flight" tool.

Rewrites a rough, sometimes-vague prompt into a clearer, better-structured one using
Claude Haiku (via the local ``claude -p`` binary) before it reaches a stronger model.

Public surface:
    enhance(raw_prompt) -> EnhanceResult     # the core engine
    ENHANCER_SYSTEM_PROMPT                    # the rewriter system prompt
"""

from prompt_enhancer.config import Config, load_config
from prompt_enhancer.engine import (
    RECURSION_GUARD_ENV,
    EnhanceResult,
    enhance,
)
from prompt_enhancer.policy import classify_prompt
from prompt_enhancer.system_prompt import ENHANCER_SYSTEM_PROMPT

__all__ = [
    "enhance",
    "EnhanceResult",
    "RECURSION_GUARD_ENV",
    "ENHANCER_SYSTEM_PROMPT",
    "Config",
    "load_config",
    "classify_prompt",
]
__version__ = "0.2.1"

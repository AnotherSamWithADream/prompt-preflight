"""Live integration tests -- they call the REAL ``claude`` binary with Haiku.

These are skipped by default. To run them::

    set PROMPT_ENHANCER_LIVE_TESTS=1   (Windows)   /   export ... (POSIX)
    pytest -m live

They consume a small amount of Haiku usage and depend on the model, so the assertions
are deliberately loose -- they check *behaviour* (faithfulness, sensible expansion),
not exact wording.
"""

import os
import shutil

import pytest

from prompt_enhancer.engine import enhance

_live_enabled = os.environ.get("PROMPT_ENHANCER_LIVE_TESTS") == "1" and shutil.which("claude")

requires_live = pytest.mark.skipif(
    not _live_enabled,
    reason="set PROMPT_ENHANCER_LIVE_TESTS=1 and have `claude` on PATH to run live tests",
)


@pytest.mark.live
@requires_live
def test_faithfulness_on_a_clear_prompt():
    raw = (
        "Refactor the function parse_dates in utils.py to use datetime.strptime "
        "and add a docstring."
    )
    result = enhance(raw)
    assert result.enhanced, f"expected enhancement, got fail-open: {result.error}"
    low = result.text.lower()
    # Every concrete identifier must survive verbatim -- the rewriter may not drop or
    # invent specifics.
    for token in ("parse_dates", "utils.py", "datetime.strptime", "docstring"):
        assert token in low, f"faithfulness violation: '{token}' missing from rewrite"


@pytest.mark.live
@requires_live
def test_sensible_expansion_on_a_vague_prompt():
    raw = "make my code faster and also can you add some tests for it"
    result = enhance(raw)
    assert result.enhanced, f"expected enhancement, got fail-open: {result.error}"
    # A vague prompt should gain clarity -- typically clarifying "Open questions" -- and
    # must not collapse to something shorter than the input.
    assert len(result.text) >= len(raw) * 0.7
    gained_questions = "?" in result.text or "open questions" in result.text.lower()
    grew = len(result.text.split()) > len(raw.split())
    assert gained_questions or grew, "expected the vague prompt to be expanded/clarified"
    # And it must not have been answered: a rewrite of a code request should not contain
    # a fabricated code block.
    assert "```" not in result.text


@pytest.mark.live
@requires_live
def test_raw_text_still_rewrites_at_engine_level():
    # The //raw bypass lives in the hook/CLI, not the engine. The engine always tries.
    result = enhance("write a haiku about static analysis and type checking in python")
    assert result.enhanced

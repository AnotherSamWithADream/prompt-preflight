"""Pytest configuration shared by the whole suite.

* Puts the project root on ``sys.path`` so ``import prompt_enhancer`` works without
  installing the package first.
* Scrubs all ``PROMPT_ENHANCER_*`` (and Anthropic auth/routing) environment variables
  and resets memoized engine state before every test, so a developer's local
  configuration -- or a previous test -- can never leak in.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in list(os.environ):
        if var.startswith("PROMPT_ENHANCER_"):
            monkeypatch.delenv(var, raising=False)
    # External auth/routing -- scrubbed so tests are deterministic regardless of machine.
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # Point config resolution at a non-existent file so every test starts from defaults.
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(tmp_path / "no-config.json"))

    # Clear memoized binary resolution / API clients so per-test mocks are honored.
    from prompt_enhancer import engine
    engine.reset_caches()
    yield

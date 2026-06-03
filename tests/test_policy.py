"""Tests for the shared enhance/skip/raw decision logic."""

from prompt_enhancer.config import Config
from prompt_enhancer.policy import classify_prompt, strip_raw


def test_long_prompt_enhances():
    d = classify_prompt(
        "please could you help me improve this rough prompt for the model thanks", Config()
    )
    assert d.action == "enhance"


def test_short_prompt_passthrough():
    assert classify_prompt("fix the bug", Config()).action == "passthrough"


def test_slash_command_passthrough():
    assert (
        classify_prompt(
            "/review this code carefully and thoroughly for any bugs at all", Config()
        ).action
        == "passthrough"
    )


def test_empty_passthrough():
    assert classify_prompt("   ", Config()).action == "passthrough"


def test_raw_bypass():
    d = classify_prompt(
        "//raw do not touch this prompt at all please keep it exactly as is", Config()
    )
    assert d.action == "raw"
    assert not d.text.startswith("//raw")
    assert d.text.startswith("do not touch")


def test_disabled_passthrough():
    cfg = Config()
    cfg.enabled = False
    assert (
        classify_prompt(
            "a long prompt that would normally be enhanced for sure here today", cfg
        ).action
        == "passthrough"
    )


def test_threshold_boundary():
    cfg = Config()  # threshold 12
    assert classify_prompt(" ".join(["word"] * 11), cfg).action == "passthrough"
    assert classify_prompt(" ".join(["word"] * 12), cfg).action == "enhance"


def test_strip_raw_helper():
    assert strip_raw("//raw hello world", "//raw") == "hello world"
    assert strip_raw("//raw", "//raw") == ""

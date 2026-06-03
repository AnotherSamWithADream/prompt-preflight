"""Tests for the output-quality / safety helpers."""

from prompt_enhancer import safety


def test_find_secret_detects_common_tokens():
    assert safety.find_secret("my key is sk-abcdefghijklmnopqrstuvwx12345")
    assert safety.find_secret("AKIAIOSFODNN7EXAMPLE here")
    assert safety.find_secret("token ghp_0123456789abcdefghijklmnopqrstuvwxyz")
    assert safety.find_secret("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123")
    assert safety.find_secret("-----BEGIN RSA PRIVATE KEY-----")
    assert safety.find_secret("nothing secret here, just a normal prompt") is None


def test_find_pii():
    assert safety.find_pii("email me at jane.doe@example.com") == "email"
    assert safety.find_pii("ssn 123-45-6789") == "ssn"
    assert safety.find_pii("a normal sentence") is None


def test_important_tokens_and_missing():
    original = "Fix `parse_dates` in utils.py and see https://example.com/docs port 8788"
    toks = safety.important_tokens(original)
    assert "utils.py" in toks
    assert "parse_dates" in toks  # backtick-quoted
    assert any("example.com" in t for t in toks)
    assert "8788" in toks
    # a rewrite that keeps them all -> nothing missing
    assert safety.missing_tokens(original, original.upper()) == []
    # dropping utils.py is flagged
    assert "utils.py" in safety.missing_tokens(
        original, "fix parse_dates and see https://example.com/docs 8788"
    )


def test_clean_output():
    assert safety.clean_output("```\nhello world\n```") == "hello world"
    assert safety.clean_output('"just quoted"') == "just quoted"
    assert safety.clean_output("Here is the rewritten prompt:\nDo the thing.") == "Do the thing."
    assert safety.clean_output("  already clean  ") == "already clean"


def test_plausible_length():
    assert safety.plausible_length("a" * 100, "b" * 100, 0.2, 12.0)
    assert not safety.plausible_length("a" * 100, "b" * 5, 0.2, 12.0)  # too short
    assert not safety.plausible_length("a" * 10, "b" * 200, 0.2, 12.0)  # runaway
    assert safety.plausible_length("", "anything", 0.2, 12.0)  # empty original -> ok

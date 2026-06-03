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
    assert not safety.plausible_length("a" * 100, "b" * 2000, 0.2, 12.0)  # runaway (> 12x, > floor)
    assert safety.plausible_length("", "anything", 0.2, 12.0)  # empty original -> ok
    # A very short prompt may expand past 12x up to the absolute floor (clarification)...
    assert safety.plausible_length("fix my code", "b" * 500, 0.2, 12.0)  # 500 < 600 floor
    # ...but not into a runaway essay.
    assert not safety.plausible_length("fix my code", "b" * 5000, 0.2, 12.0)


def test_path_token_ignores_prose_slashes():
    # Prose with slashes must NOT become must-keep-verbatim tokens, or the default-on
    # faithfulness check would wrongly reject good rewrites of common prompts.
    for prose in ("handle input/output", "use TCP/IP", "read and/or write", "due 12/25/2024"):
        assert all("/" not in t for t in safety.important_tokens(prose))
    # a genuinely rooted / drive-qualified path IS still a hard token
    assert "/etc/hosts" in safety.important_tokens("edit /etc/hosts now")
    assert any("config" in t for t in safety.important_tokens(r"open C:\app\config now"))


def test_faithfulness_allows_reworded_slash_prose():
    assert (
        safety.missing_tokens(
            "optimize read/write over TCP/IP",
            "Improve reading and writing over the network protocol.",
        )
        == []
    )


def test_preamble_preserves_genuine_first_line():
    # A meta-preamble that names the artifact is stripped...
    assert safety.clean_output("Here is the improved prompt:\nDo X.") == "Do X."
    # ...but a real first line that merely starts with "Here are X:" is preserved.
    assert safety.clean_output("Here are the requirements:\n- a\n- b").startswith(
        "Here are the requirements:"
    )

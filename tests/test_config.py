"""Tests for the config system: defaults, file loading, env overrides, proxy match."""

import json

from prompt_enhancer.config import (
    Config,
    is_local_proxy,
    load_config,
    points_at_proxy,
    validate,
    write_template,
)


def test_defaults():
    cfg = load_config()
    assert cfg.backend == "auto"
    assert cfg.word_threshold == 12
    assert cfg.proxy_port == 8788
    assert cfg.bypass_prefix == "//raw"


def test_file_overrides(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"backend": "cli", "word_threshold": 5, "proxy_port": 9999}))
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(p))
    cfg = load_config()
    assert cfg.backend == "cli"
    assert cfg.word_threshold == 5
    assert cfg.proxy_port == 9999


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"backend": "cli", "word_threshold": 8}))
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(p))
    monkeypatch.setenv("PROMPT_ENHANCER_BACKEND", "api")
    monkeypatch.setenv("PROMPT_ENHANCER_WORD_THRESHOLD", "3")
    cfg = load_config()
    assert cfg.backend == "api"  # env beats file
    assert cfg.word_threshold == 3


def test_malformed_file_falls_back_to_defaults(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text("{ this is not valid json")
    monkeypatch.setenv("PROMPT_ENHANCER_CONFIG", str(p))
    cfg = load_config()
    assert cfg.backend == "auto"  # never breaks; defaults stand


def test_skip_models_from_csv_env(monkeypatch):
    monkeypatch.setenv("PROMPT_ENHANCER_PROXY_SKIP_MODELS", "haiku, tiny")
    cfg = load_config()
    assert cfg.proxy_skip_models == ("haiku", "tiny")


def test_points_at_proxy():
    cfg = Config()  # 127.0.0.1:8788
    assert points_at_proxy("http://127.0.0.1:8788", cfg)
    assert points_at_proxy("http://localhost:8788", cfg)
    assert not points_at_proxy("https://api.anthropic.com", cfg)
    assert not points_at_proxy("http://127.0.0.1:9999", cfg)
    assert not points_at_proxy(None, cfg)


def test_write_template(tmp_path):
    p = tmp_path / "sub" / "config.json"
    write_template(str(p))
    data = json.loads(p.read_text())
    assert data["backend"] == "auto"
    assert isinstance(data["proxy_skip_models"], list)  # JSON-friendly


def test_new_field_defaults():
    cfg = Config()
    assert cfg.max_prompt_chars == 100_000
    assert cfg.api_retries == 1
    assert cfg.allow_public_bind is False
    assert cfg.proxy_max_concurrency >= 1
    assert cfg.proxy_reminder_marker == "<system-reminder"


def test_validate_catches_bad_values():
    cfg = Config()
    cfg.backend = "xyz"
    cfg.proxy_port = 0
    cfg.timeout = 0
    problems = validate(cfg)
    assert any("backend" in p for p in problems)
    assert any("proxy_port" in p for p in problems)
    assert any("timeout" in p for p in problems)
    assert validate(Config()) == []  # defaults are valid


def test_validate_new_enums():
    cfg = Config()
    cfg.api_provider = "nope"
    cfg.hook_output_style = "loud"
    problems = validate(cfg)
    assert any("api_provider" in p for p in problems)
    assert any("hook_output_style" in p for p in problems)
    # heuristic is a first-class backend now
    ok = Config()
    ok.backend = "heuristic"
    assert validate(ok) == []


def test_is_local_proxy_any_loopback_port():
    cfg = Config()  # proxy_port 8788
    # Non-default port still recognized as a local proxy (the hook fix #51)
    assert is_local_proxy("http://127.0.0.1:9999", cfg)
    assert is_local_proxy("http://localhost:1234", cfg)
    assert not is_local_proxy("https://api.anthropic.com", cfg)
    assert not is_local_proxy(None, cfg)

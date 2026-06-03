"""Doc/schema contract tests (#40).

These keep cross-module assumptions and the README in lockstep with the code, so the
documentation and validation tables can't silently drift from the actual dataclass /
engine. They are pure (no I/O beyond reading README.md) and fast.
"""

from dataclasses import fields
from pathlib import Path

from prompt_enhancer import engine
from prompt_enhancer.config import (
    _ENV_MAP,
    _VALID_API_PROVIDERS,
    _VALID_BACKENDS,
    _VALID_HOOK_STYLES,
    _VALID_PROFILES,
    Config,
    to_dict,
)
from prompt_enhancer.system_prompt import _PROFILE_SUFFIXES

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")

#: Fields deliberately settable only via the config file (numeric guard rails), so the
#: README's "every config field has a PROMPT_ENHANCER_<FIELD> override" is allowed these
#: documented exceptions. Adding a new field tightens this set on purpose.
_NO_ENV_OVERRIDE = {"length_ratio_min", "length_ratio_max", "circuit_breaker_cooldown"}


def test_env_map_targets_are_real_fields():
    names = {f.name for f in fields(Config)}
    for env, target in _ENV_MAP.items():
        assert target in names, f"{env} -> {target!r} is not a Config field"
        assert env.startswith("PROMPT_ENHANCER_"), f"{env} breaks the env-var naming convention"


def test_env_override_coverage_matches_readme_promise():
    names = {f.name for f in fields(Config)}
    missing = names - set(_ENV_MAP.values())
    assert missing == _NO_ENV_OVERRIDE, (
        "config fields without a PROMPT_ENHANCER_* override changed; update _ENV_MAP or the "
        f"documented exception set. Unexpected: {missing ^ _NO_ENV_OVERRIDE}"
    )


def test_to_dict_round_trips_every_field():
    # The serialised config the CLI shows/writes must cover exactly the dataclass fields.
    assert set(to_dict(Config())) == {f.name for f in fields(Config)}


def test_valid_profiles_match_system_prompt():
    assert set(_VALID_PROFILES) == set(_PROFILE_SUFFIXES)


def test_valid_backends_cover_engine_builtins():
    # Every backend the engine can dispatch is an accepted config value (plus "auto").
    assert set(engine._BUILTIN_BACKENDS) <= set(_VALID_BACKENDS)
    assert "auto" in _VALID_BACKENDS


def test_valid_enum_sets_are_nonempty():
    assert _VALID_API_PROVIDERS and _VALID_HOOK_STYLES


def test_readme_documents_core_surface():
    # The user-facing entry points and privacy switches must stay documented.
    for token in (
        "enhance-cli",
        "enhance-hook",
        "PROMPT_ENHANCER_LOG",
        "PROMPT_ENHANCER_LOG_CONTENT",
        "PROMPT_ENHANCER_PROXY_DEBUG",
        "//raw",
    ):
        assert token in README, f"{token!r} is no longer documented in README.md"

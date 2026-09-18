"""Tests for the metadata-only usage ledger.

The privacy test is the important one: the ledger must be able to answer "is this working
and what is it costing" without ever storing a single character of prompt text.
"""

from prompt_enhancer import ledger
from prompt_enhancer.config import Config


def _cfg(tmp_path, **kw):
    cfg = Config()
    cfg.ledger = True
    cfg.ledger_path = str(tmp_path / "l.jsonl")
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_record_and_read_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    ledger.record(
        cfg,
        event="enhanced",
        backend="cli",
        profile="coding",
        elapsed=1.25,
        chars_in=50,
        chars_out=200,
        cost_usd=0.0093,
    )
    records = ledger.read_records(cfg)
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "enhanced"
    assert rec["backend"] == "cli"
    assert rec["profile"] == "coding"
    assert rec["chars_in"] == 50 and rec["chars_out"] == 200
    assert rec["cost_usd"] == 0.0093
    assert rec["ts"]


def test_ledger_never_stores_prompt_text(tmp_path):
    cfg = _cfg(tmp_path)
    confidential = "ACME-CLIENT-MERGER-CODENAME-BLUEBIRD"
    ledger.record(cfg, event="enhanced", backend="cli", chars_in=len(confidential), chars_out=9)
    blob = (tmp_path / "l.jsonl").read_text(encoding="utf-8")
    assert confidential not in blob  # only the LENGTH is recorded, never the text
    assert f'"chars_in": {len(confidential)}' in blob


def test_disabled_ledger_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path, ledger=False)
    ledger.record(cfg, event="enhanced", backend="cli")
    assert not (tmp_path / "l.jsonl").exists()


def test_record_never_raises_on_bad_path(tmp_path):
    # A directory is not writable as a file -- the ledger must swallow it, not explode.
    cfg = _cfg(tmp_path)
    cfg.ledger_path = str(tmp_path)
    ledger.record(cfg, event="enhanced", backend="cli")  # must not raise


def test_summarize_counts_and_rates():
    recs = [
        {
            "event": "enhanced",
            "elapsed": 1.0,
            "chars_in": 10,
            "chars_out": 40,
            "cost_usd": 0.01,
            "project": "alpha",
            "ts": "2026-09-18T01:00:00+00:00",
        },
        {
            "event": "enhanced",
            "elapsed": 3.0,
            "chars_in": 10,
            "chars_out": 40,
            "cost_usd": 0.01,
            "project": "alpha",
            "ts": "2026-09-18T02:00:00+00:00",
        },
        {"event": "fail-open", "reason": "timeout", "ts": "2026-09-18T03:00:00+00:00"},
        {"event": "skip", "reason": "well-formed", "ts": "2026-09-18T04:00:00+00:00"},
    ]
    s = ledger.summarize(recs)
    assert s["enhanced"] == 2
    assert s["fail_open"] == 1
    assert s["skipped"] == 1
    assert s["attempts"] == 3  # skips are not attempts
    assert abs(s["cost_usd"] - 0.02) < 1e-9
    assert s["reasons"]["timeout"] == 1
    assert s["projects"]["alpha"] == 2
    assert s["by_day"]["2026-09-18"] == 2


def test_read_tail_returns_recent_records(tmp_path):
    cfg = _cfg(tmp_path)
    for i in range(40):
        ledger.record(cfg, event="enhanced", backend="cli", chars_in=i)
    tail = ledger.read_tail(cfg)
    assert len(tail) == 40
    assert tail[-1]["chars_in"] == 39

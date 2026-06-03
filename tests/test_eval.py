"""Test for the offline eval harness (#39).

Runs the harness against the deterministic, network-free ``heuristic`` backend and
asserts every invariant holds -- so a regression in the faithfulness / length / secret
guards (or the heuristic backend itself) is caught in CI without touching a model.
"""

import os
import sys

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, _SCRIPTS)

eval_prompts = pytest.importorskip("eval_prompts")


def test_heuristic_backend_holds_all_invariants():
    report = eval_prompts.evaluate("heuristic")
    failures = [r for r in report if not r["ok"]]
    assert not failures, f"eval invariants failed for: {[r['id'] for r in failures]}"


def test_secret_case_is_skipped_not_rewritten():
    rows = {r["id"]: r for r in eval_prompts.evaluate("heuristic")}
    assert rows["secret"]["enhanced"] is False
    assert rows["secret"]["secret_detected"] is True


def test_main_returns_zero_on_clean_run(capsys):
    assert eval_prompts.main(["--backend", "heuristic", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"passed": 6' in out

#!/usr/bin/env python3
"""Offline eval harness for the enhancement engine (#39).

Runs a fixed battery of synthetic prompts through a chosen backend and checks the
invariants that matter regardless of wording quality:

* faithfulness  -- every "hard" token (file paths, URLs, code spans, numbers) survives;
* plausibility  -- the rewrite length stays within the configured ratio band;
* secret-safety -- a prompt carrying a credential is never sent to the enhancer.

It is deliberately model-agnostic: point it at ``--backend heuristic`` for a fast,
deterministic, network-free run (used by the test-suite), or at ``cli`` / ``api`` to
spot-check the real model. Synthetic fixtures only -- no real secrets, nothing logged.

    python scripts/eval_prompts.py --backend heuristic
    python scripts/eval_prompts.py --backend cli --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_enhancer.config import load_config  # noqa: E402
from prompt_enhancer.engine import enhance  # noqa: E402
from prompt_enhancer.safety import find_secret, missing_tokens, plausible_length  # noqa: E402

#: Each case: an id, the raw prompt, and whether enhancement is expected to be skipped
#: (e.g. because it carries a secret). Synthetic content only.
CASES = [
    ("paths", "fix the bug in src/app/main.py and update tests/test_main.py please now", False),
    ("url", "summarize the docs at https://example.com/guide/v2 in three bullet points", False),
    ("code", "explain what `git rebase -i HEAD~3` does and when I would use it instead", False),
    ("numbers", "scale the worker pool from 4 to 16 and set the timeout to 30000 ms", False),
    ("plain", "make this rough idea into a clearer and better structured request please", False),
    ("secret", "use my key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 to call the api for me", True),
]


def evaluate(backend: str, cfg=None) -> list[dict]:
    """Run every case through ``backend`` and return a per-case invariant report."""
    cfg = cfg if cfg is not None else load_config()
    report = []
    for case_id, prompt, expect_skip in CASES:
        result = enhance(prompt, backend=backend, config=cfg)
        row: dict = {
            "id": case_id,
            "backend": result.backend,
            "enhanced": result.enhanced,
            "error": result.error,
        }
        if expect_skip:
            # A credential-bearing prompt must be skipped (secret detector), not rewritten.
            row["secret_detected"] = find_secret(prompt) is not None
            row["ok"] = (not result.enhanced) and row["secret_detected"]
        elif result.enhanced:
            missing = missing_tokens(prompt, result.text)
            length_ok = plausible_length(
                prompt, result.text, cfg.length_ratio_min, cfg.length_ratio_max
            )
            row["missing_tokens"] = missing
            row["length_ok"] = length_ok
            row["ok"] = not missing and length_ok
        else:
            # Fail-open is acceptable (e.g. no backend available) but flagged, not a pass.
            row["ok"] = False
        report.append(row)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="eval_prompts", description=__doc__)
    parser.add_argument("--backend", default="heuristic")
    parser.add_argument("--json", action="store_true", help="Emit the raw report as JSON.")
    args = parser.parse_args(argv)

    report = evaluate(args.backend)
    passed = sum(1 for r in report if r["ok"])
    if args.json:
        json.dump({"backend": args.backend, "passed": passed, "report": report}, sys.stdout)
        sys.stdout.write("\n")
    else:
        for r in report:
            mark = "PASS" if r["ok"] else "FAIL"
            extra = "" if r["ok"] else f"  ({ {k: v for k, v in r.items() if k != 'id'} })"
            sys.stdout.write(f"  [{mark}] {r['id']}{extra}\n")
        sys.stdout.write(f"\n{passed}/{len(report)} invariants held (backend={args.backend}).\n")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Local, metadata-only usage ledger.

Records one JSON line per enhancement decision so ``enhance-cli stats`` / ``digest`` /
``statusline`` can answer "is this actually working, how often does it fail open, what is
it costing?" -- without mining Claude Code transcripts.

PRIVACY: prompt text is **never** written here. Only lengths, timings, decision codes and
the project directory name. The file is local-only and never transmitted. Turn it off with
``ledger = false`` (or ``PROMPT_ENHANCER_LEDGER=0``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

#: Trim the file once it grows past this many bytes (keeps the newest records).
_TRIM_BYTES = 4_000_000


def ledger_path(cfg) -> str:
    """Where the ledger lives (honours ``ledger_path``, else a per-user state dir)."""
    explicit = getattr(cfg, "ledger_path", "") or os.environ.get("PROMPT_ENHANCER_LEDGER_PATH", "")
    if explicit:
        return explicit
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "prompt-enhancer", "ledger.jsonl")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "prompt-enhancer", "ledger.jsonl")


def record(
    cfg,
    *,
    event: str,
    backend=None,
    profile=None,
    elapsed: float = 0.0,
    chars_in: int = 0,
    chars_out: int = 0,
    cost_usd=None,
    reason=None,
) -> None:
    """Append one metadata-only record. Never raises -- observability must not break
    enhancement, and must never be the reason a prompt fails."""
    if not getattr(cfg, "ledger", False):
        return
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,  # enhanced | fail-open | skip
            "backend": backend,
            "profile": profile,
            "elapsed": round(float(elapsed), 3),
            "chars_in": int(chars_in),
            "chars_out": int(chars_out),
        }
        if cost_usd is not None:
            rec["cost_usd"] = round(float(cost_usd), 6)
        if reason:
            rec["reason"] = reason
        try:
            rec["project"] = os.path.basename(os.getcwd()) or "?"
        except OSError:
            pass
        path = ledger_path(cfg)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        _maybe_trim(path)
    except Exception:  # noqa: BLE001 -- never let the ledger break the engine
        pass


def _maybe_trim(path: str) -> None:
    try:
        if os.path.getsize(path) < _TRIM_BYTES:
            return
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[len(lines) // 2 :]  # drop the oldest half
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
    except Exception:  # noqa: BLE001
        pass


def read_records(cfg, *, days: int | None = None) -> list:
    """Read ledger records, optionally only the last ``days``. Never raises."""
    path = ledger_path(cfg)
    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if cutoff is not None:
                    ts = _parse_ts(rec.get("ts"))
                    if ts is None or ts < cutoff:
                        continue
                out.append(rec)
    except OSError:
        return []
    return out


def _parse_ts(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def summarize(records: list) -> dict:
    """Aggregate records into the numbers stats/digest/statusline display."""
    enhanced = [r for r in records if r.get("event") == "enhanced"]
    failed = [r for r in records if r.get("event") == "fail-open"]
    skipped = [r for r in records if r.get("event") == "skip"]
    attempts = len(enhanced) + len(failed)
    cost = sum(float(r.get("cost_usd") or 0) for r in records)
    elapsed = [float(r.get("elapsed") or 0) for r in enhanced if r.get("elapsed")]
    reasons: dict = {}
    for r in failed:
        key = str(r.get("reason") or "unknown").split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1
    projects: dict = {}
    for r in enhanced:
        p = r.get("project") or "?"
        projects[p] = projects.get(p, 0) + 1
    days: dict = {}
    for r in enhanced:
        d = str(r.get("ts") or "")[:10]
        if d:
            days[d] = days.get(d, 0) + 1
    return {
        "records": len(records),
        "enhanced": len(enhanced),
        "fail_open": len(failed),
        "skipped": len(skipped),
        "attempts": attempts,
        "success_rate": (len(enhanced) / attempts) if attempts else 0.0,
        "cost_usd": round(cost, 4),
        "median_elapsed": round(sorted(elapsed)[len(elapsed) // 2], 2) if elapsed else 0.0,
        "chars_in": sum(int(r.get("chars_in") or 0) for r in enhanced),
        "chars_out": sum(int(r.get("chars_out") or 0) for r in enhanced),
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "projects": dict(sorted(projects.items(), key=lambda kv: -kv[1])),
        "by_day": dict(sorted(days.items())),
    }


def month_to_date_cost(cfg) -> float:
    """Spend since the 1st of the current (UTC) month -- used by the budget cap."""
    now = datetime.now(timezone.utc)
    total = 0.0
    for rec in read_records(cfg):
        ts = _parse_ts(rec.get("ts"))
        if ts and ts.year == now.year and ts.month == now.month:
            total += float(rec.get("cost_usd") or 0)
    return round(total, 6)


def read_tail(cfg, max_bytes: int = 65536) -> list:
    """Read only the last ``max_bytes`` of the ledger -- the fast path for the statusline,
    which may be rendered on every prompt and must not re-read a large file."""
    path = ledger_path(cfg)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # discard the partial first line
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in data.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def today_records(cfg) -> list:
    """Records from the current UTC day (statusline / digest 'today' figures)."""
    today = datetime.now(timezone.utc).date().isoformat()
    return [r for r in read_tail(cfg) if str(r.get("ts") or "").startswith(today)]

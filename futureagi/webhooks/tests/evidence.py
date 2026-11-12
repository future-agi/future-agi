"""DESIGN.md section 17.1's evidence for the WH-1b rows: each row writes ``webhooks/.evidence/<row>.json`` with what
the test measured, the receiver's log lines and the DB rows it asserted on, so a reviewer can read a run without
re-running it. The run's head is ``OMEGA_EXEC_SOURCE_HEAD`` (cloud exec sets it); the cloud wrapper pulls the folder.

A row with several tests keeps one entry per test. The evidence is written before a test's assertions, so a failed
run leaves its numbers too; ``passed`` is not recorded here (the JUnit file says that).

Nothing secret is written: receiver lines keep only the delivery headers (``webhook-id``, ``X-FutureAGI-*``), never a
signature, a body or a custom header; DB rows are passed in by the test as plain fields.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / ".evidence"
SAFE_HEADERS = (
    "webhook-id",
    "x-futureagi-delivery",
    "x-futureagi-attempt",
    "x-futureagi-sequence",
    "x-futureagi-event",
    "x-futureagi-test",
)
_lock = threading.Lock()


def receiver_lines(receiver, path: str | None = None, limit: int = 60) -> dict:
    """The receiver's log: the total, and the first ``limit`` lines (per path when ``path`` is None), each with its
    arrival time in seconds since the receiver started, the path, the safe headers and the body size."""
    with receiver.lock:
        reqs = [r for r in receiver.requests if path is None or r["path"] == path]
    lines: dict[str, list[dict]] = {}
    for r in reqs:
        kept = lines.setdefault(r["path"], [])
        if len(kept) < limit:
            kept.append(
                {
                    "at_s": round(r["at"] - receiver.t0, 3),
                    **{h: r["headers"][h] for h in SAFE_HEADERS if h in r["headers"]},
                    "body_bytes": len(r["body"]),
                }
            )
    counts: dict[str, int] = {}
    for r in reqs:
        counts[r["path"]] = counts.get(r["path"], 0) + 1
    return {"requests": len(reqs), "by_path": counts, "first_lines": lines}


def by_status(queryset, field: str = "status") -> dict[str, int]:
    """Row counts per value of ``field`` (a DB-rows summary)."""
    from django.db.models import Count

    return {
        str(k): n
        for k, n in queryset.values_list(field).annotate(n=Count("pk")).order_by(field)
    }


def write(row: str, **data) -> Path:
    """Record ``data`` for the running test under ``row``, added to what the test wrote before; returns the file."""
    test = os.environ.get("PYTEST_CURRENT_TEST", "unknown").split(" ")[0]
    path = DIR / f"{row}.json"
    with _lock:
        DIR.mkdir(exist_ok=True)
        doc = (
            json.loads(path.read_text())
            if path.exists()
            else {"row": row, "design": "DESIGN.md section 17.2", "tests": {}}
        )
        doc["head"] = os.environ.get("OMEGA_EXEC_SOURCE_HEAD", "unknown")
        # A test that writes twice (its measurements, then its checks) keeps both: later keys are added to its entry,
        # never replace it.
        entry = doc["tests"].setdefault(test, {})
        entry.update(data)
        entry["written_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    return path

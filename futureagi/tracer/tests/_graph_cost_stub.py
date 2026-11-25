"""One analytics stub that can answer a filtered graph's routing cost probe.

A filtered Observe graph costs its own scan from the part index before it picks
a lane. An analytics object that cannot answer that probe leaves the read
UNCOSTED, and an uncosted read is routed to the background worker on purpose -
issuing it on the interactive wall is the defect the gate exists to remove.

Tests whose subject is WHICH INLINE READER a wrapper calls, rather than the
routing decision itself, therefore need the probe answered and nothing else.
They used a bare ``object()`` placeholder, which cannot. This supplies the one
answer they need, deliberately an affordable one. The gate's own behaviour -
including what an unanswerable probe must do - is pinned in
``test_graph_read_cost_gate.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

ESTIMATE_COLUMNS = ["database", "table", "parts", "rows", "marks"]


def affordable_scan_estimate(rows: int = 1_000) -> SimpleNamespace:
    """One ``EXPLAIN ESTIMATE`` result the cost reducer will accept."""

    return SimpleNamespace(
        data=[
            {
                "database": "default",
                "table": "spans",
                "parts": 1,
                "rows": int(rows),
                "marks": 1,
            }
        ],
        columns=list(ESTIMATE_COLUMNS),
        query_time_ms=1,
    )


class AffordableScanAnalytics:
    """Answer the cost probe; refuse anything else, loudly."""

    supports_per_query_read_settings = True

    def __init__(self, rows: int = 1_000) -> None:
        self.rows = int(rows)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.calls.append((query, dict(params or {})))
        assert "EXPLAIN ESTIMATE" in query, (
            "this stub answers the routing cost probe only; a test that reaches "
            "a real statement needs an analytics fake that returns graph rows"
        )
        return affordable_scan_estimate(self.rows)

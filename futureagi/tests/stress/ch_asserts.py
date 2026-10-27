"""CH query-budget helpers: tag eval-path queries with a ``log_comment``,
then assert `system.query_log` metrics (read_rows / memory_usage / …) and
query counts against the named constants in ``budgets.py``.
"""

from __future__ import annotations

from contextlib import contextmanager

import clickhouse_connect

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.query_settings import ch_query_settings

_FIELDS = ("memory_usage", "read_rows", "read_bytes", "query_duration_ms")

# Server-side timestamp captured before any test query runs (see
# ``capture_baseline``); bounds the query_log scan so tagged rows left by
# previous pytest sessions against the same long-lived CH don't count.
_baseline = None


def _client():
    cfg = get_v2_config()
    return clickhouse_connect.get_client(
        host=cfg["host"],
        port=cfg["http_port"],
        username=cfg["user"],
        password=cfg["password"] or "",
        database=cfg["database"],
    )


def capture_baseline() -> None:
    """Record the CH server clock; called once by the session fixture before
    any test issues tagged queries."""
    global _baseline
    client = _client()
    try:
        _baseline = client.command("SELECT now64(6)")
    finally:
        client.close()


def _query_log_rows(tag: str) -> list[dict]:
    """All QueryFinish rows tagged ``tag`` since the session baseline."""
    client = _client()
    try:
        client.command("SYSTEM FLUSH LOGS")
        where = "log_comment = %(t)s AND type = 'QueryFinish'"
        params: dict = {"t": tag}
        if _baseline is not None:
            where += " AND event_time_microseconds >= %(b)s"
            params["b"] = _baseline
        rows = client.query(
            f"SELECT {', '.join(_FIELDS)} FROM system.query_log WHERE {where}",
            parameters=params,
        ).result_rows
    finally:
        client.close()
    return [dict(zip(_FIELDS, r, strict=True)) for r in rows]


class BudgetResult:
    """Aggregates the tagged queries' ``system.query_log`` rows."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def total(self, field: str) -> float:
        return sum(r[field] for r in self.rows)

    def max(self, field: str) -> float:
        return max((r[field] for r in self.rows), default=0)

    @property
    def count(self) -> int:
        return len(self.rows)


@contextmanager
def ch_query_budget(tag: str):
    """Tag every CH query in the block with ``tag``; on exit flush logs and
    load the tagged QueryFinish rows into the yielded ``BudgetResult``."""
    result = BudgetResult()
    with ch_query_settings(log_comment=tag):
        yield result
    result.rows = _query_log_rows(tag)

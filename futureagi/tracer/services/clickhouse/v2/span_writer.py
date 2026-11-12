"""Post-commit PG span snapshots -> v2 ClickHouse spans, without legacy CDC.

Callers capture the rows inside their PG transaction and schedule the mirror
with on_commit. Versions come from the saved PG revision, not callback time:
retries are idempotent and a delayed older callback cannot beat a newer update.
Like the trace/curated writers this is best-effort; failures are logged, and PG
retains the rows for reconciliation.
"""

from copy import deepcopy
from datetime import UTC, datetime

import structlog

from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.adapter import (
    CH_INSERT_COLUMNS,
    EXPECTED_PG_COLUMNS,
    adapt,
    row_to_tuple,
)

log = structlog.get_logger("ch25.span_writer")
_COLUMNS = (*CH_INSERT_COLUMNS, "_version")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def span_to_pg_row(span, trace_session_id):
    """Capture adapter fields without lazy FK lookups or retaining model objects."""
    return deepcopy(
        {
            **{
                name: getattr(span, name)
                for name in EXPECTED_PG_COLUMNS
                if name != "trace_session_id"
            },
            "trace_session_id": trace_session_id,
        }
    )


def _version(row):
    # PG stores microseconds. Integer arithmetic avoids float timestamp rounding.
    delta = row.updated_at - _EPOCH
    return (
        (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    ) * 1000


def _physical_key(row):
    # Mirrors the table ORDER BY, including its time bucket/partition. The
    # synthetic root can move to an earlier hour when another batch arrives.
    return (
        row.project_id,
        row.observation_type,
        row.service_name,
        row.start_time.replace(minute=0, second=0, microsecond=0),
        row.trace_id,
        row.id,
    )


def mirror_spans_to_clickhouse(spans, *, previous_spans=()):
    """Insert committed adapter rows with explicit ReplacingMergeTree versions.

    Retire the old physical key when an update changes its time bucket/type;
    otherwise a synthetic root whose earliest child changed would appear twice.
    An explicit span flag overrides the cutover default independently of traces.
    """
    import os

    explicit = os.getenv("CH25_SPAN_DUAL_WRITE")
    if not trace_writer._truthy(
        explicit if explicit is not None else os.getenv("CH25_DROP_LEGACY_CDC_CHAIN")
    ):
        return
    try:
        previous = {row["id"]: row for row in previous_spans}
        rows = []
        for pg_row in spans:
            current = adapt(pg_row)
            version = _version(current)
            old_pg_row = previous.get(pg_row["id"])
            if old_pg_row is not None:
                old = adapt(old_pg_row)
                if _physical_key(old) != _physical_key(current):
                    old.is_deleted = 1
                    rows.append((*row_to_tuple(old), version))
            rows.append((*row_to_tuple(current), version))
        if rows:
            trace_writer._get_client().insert(
                "spans", rows, column_names=list(_COLUMNS)
            )
    except Exception:
        log.exception("span_dual_write_failed")
        trace_writer._reset_client()

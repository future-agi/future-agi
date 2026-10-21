"""Last-write reads for dataset columns that hold ``running`` cells.

PostgreSQL cannot say when a cell last changed: the runners flip cells to
``running`` with ``.update()`` and ``bulk_update()``, which never bump
``updated_at``. The ClickHouse mirror can: CDC re-lands every written row, so
``_peerdb_synced_at`` moves on each write, bulk or not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from model_hub.models.choices import CellStatus
from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)

_READ_TIMEOUT_MS = 60_000

# Newest first, so a capped answer drops the oldest (stale) columns and never
# the fresh one that proves a run is alive.
MAX_RUNNING_COLUMNS = 10_000

_RUNNING_COLUMNS_SQL = """
SELECT
    toString(cell_column) AS column_id,
    countIf(cell_status = %(running)s AND cell_deleted = 0 AND cell_is_deleted = 0)
        AS running_cells,
    max(cell_last_write) AS last_write
FROM (
    -- Aliases differ from the columns: an alias named like a column would
    -- replace it in this query's WHERE.
    SELECT
        id,
        any(column_id) AS cell_column,
        argMax(status, _peerdb_version) AS cell_status,
        argMax(deleted, _peerdb_version) AS cell_deleted,
        argMax(_peerdb_is_deleted, _peerdb_version) AS cell_is_deleted,
        max(_peerdb_synced_at) AS cell_last_write
    FROM model_hub_cell
    WHERE column_id IN (
        SELECT column_id FROM model_hub_cell WHERE status = %(running)s
    )
    GROUP BY id
)
GROUP BY cell_column
HAVING running_cells > 0
ORDER BY last_write DESC
LIMIT %(limit)s
"""

_MIRROR_LAST_WRITE_SQL = (
    "SELECT max(_peerdb_synced_at), max(updated_at) FROM model_hub_cell"
)


@dataclass(frozen=True)
class RunningColumn:
    """A column holding ``running`` cells, and when any of its cells last changed."""

    column_id: str
    running_cells: int
    last_write: datetime


def _as_utc(value: datetime) -> datetime:
    # The mirror's DateTime64 columns are UTC and come back naive.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def mirror_is_available() -> bool:
    return is_clickhouse_enabled()


def read_mirror_last_write() -> datetime | None:
    """How current the cell mirror is; None when it holds nothing.

    The older of when it last applied a write and the newest Postgres write
    stamp it holds: while CDC replays a backlog, the first is fresh and the
    mirror still lacks everything written after the second.
    """
    rows, _, _ = get_clickhouse_client().execute_read(
        _MIRROR_LAST_WRITE_SQL, timeout_ms=_READ_TIMEOUT_MS
    )
    if not rows or not all(rows[0]):
        return None
    return min(_as_utc(value) for value in rows[0])


def read_running_columns() -> list[RunningColumn]:
    """Every column with a live ``running`` cell, newest last write first.

    ``last_write`` covers all of the column's cells, finished ones included, so
    a run writing results into a column keeps the whole column fresh.
    """
    rows, _, _ = get_clickhouse_client().execute_read(
        _RUNNING_COLUMNS_SQL,
        {"running": CellStatus.RUNNING.value, "limit": MAX_RUNNING_COLUMNS},
        timeout_ms=_READ_TIMEOUT_MS,
    )
    return [
        RunningColumn(
            column_id=column_id,
            running_cells=int(running),
            last_write=_as_utc(last_write),
        )
        for column_id, running, last_write in rows
    ]

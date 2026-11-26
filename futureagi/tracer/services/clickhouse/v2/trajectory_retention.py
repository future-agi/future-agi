"""ClickHouse retention and project-deletion cleanup for ``trajectory_items``.

``040_trajectory_items.sql`` deliberately declares no table-level TTL (mirroring
``020_remove_ttls.sql`` so retention stays controlled by plan policy and erasure
workflows).  This module provides bounded, idempotent ClickHouse deletion helpers
that safely no-op when ``040_trajectory_items.sql`` has not yet been applied.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_PROJECT_DELETE_BATCH = 200


def _table_exists(client: Any, table_name: str = "trajectory_items") -> bool:
    """Return True iff ``table_name`` exists in the client's current database."""
    try:
        rows = client.query(
            "SELECT count() FROM system.tables "
            "WHERE database = currentDatabase() AND name = %(tbl)s",
            parameters={"tbl": table_name},
        ).result_rows
        return bool(rows and int(rows[0][0]) > 0)
    except Exception:
        logger.warning(
            "trajectory_items_table_check_failed", table=table_name, exc_info=True
        )
        return False


def _open_client(client: Any | None) -> tuple[Any | None, bool]:
    if client is not None:
        return client, False
    try:
        from tracer.services.clickhouse.v2 import get_reader

        reader = get_reader()
        return reader._client, True
    except Exception:
        logger.warning("trajectory_items_client_open_failed", exc_info=True)
        return None, False


def delete_project_trajectory_items(
    project_ids: Sequence[str], *, client: Any | None = None
) -> int:
    """Delete all ``trajectory_items`` rows belonging to deleted projects.

    Safe to call unconditionally from ``soft_delete_projects`` on_commit hooks:
    no-ops cleanly if ``trajectory_items`` has not been created yet or if
    ClickHouse is unavailable (logged as a warning so PG project deletion is
    never blocked).  Returns the number of project ids scheduled for deletion.
    """
    pids = tuple(dict.fromkeys(str(pid) for pid in project_ids if pid))
    if not pids:
        return 0

    ch_client, should_close = _open_client(client)
    if ch_client is None:
        return 0
    deleted = 0
    try:
        if not _table_exists(ch_client):
            return 0
        for offset in range(0, len(pids), _PROJECT_DELETE_BATCH):
            batch = pids[offset : offset + _PROJECT_DELETE_BATCH]
            ch_client.command(
                "ALTER TABLE trajectory_items DELETE "
                "WHERE project_id IN %(pids)s "
                "SETTINGS mutations_sync = 0",
                parameters={"pids": batch},
            )
            deleted += len(batch)
    except Exception:
        logger.warning(
            "trajectory_items_project_delete_failed",
            project_count=len(pids),
            exc_info=True,
        )
    finally:
        if should_close:
            with contextlib.suppress(Exception):
                ch_client.close()
    return deleted


def purge_expired_trajectory_items(
    *,
    project_id: str,
    cutoff: datetime,
    client: Any | None = None,
) -> bool:
    """Delete ``trajectory_items`` older than ``cutoff`` for one project.

    Intended to be invoked alongside span retention sweeps so deduplicated
    trajectory content never outlives its project's retention window.
    """
    if not project_id:
        return False
    utc_cutoff = (
        cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff.astimezone(UTC)
    )
    cutoff_str = utc_cutoff.strftime("%Y-%m-%d %H:%M:%S.%f")

    ch_client, should_close = _open_client(client)
    if ch_client is None:
        return False
    try:
        if not _table_exists(ch_client):
            return False
        ch_client.command(
            "ALTER TABLE trajectory_items DELETE "
            "WHERE project_id = toUUID(%(pid)s) "
            "AND first_seen_at < toDateTime64(%(cutoff)s, 6, 'UTC') "
            "SETTINGS mutations_sync = 0",
            parameters={"pid": str(project_id), "cutoff": cutoff_str},
        )
        return True
    except Exception:
        logger.warning(
            "trajectory_items_retention_purge_failed",
            project_id=str(project_id),
            cutoff=cutoff_str,
            exc_info=True,
        )
        return False
    finally:
        if should_close:
            with contextlib.suppress(Exception):
                ch_client.close()

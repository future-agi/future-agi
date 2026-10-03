"""Stable, exact pagination for the simulation evaluation preview.

The regular simulation grids are intentionally live and use numbered pages.
That is a poor fit for the evaluation preview: a run/call inserted or updated
between page requests can shift an offset and make a supposedly complete list
silently skip or repeat a row.  This module binds a signed continuation to an
immutable ordering boundary and the initial membership revision.  Any drift is
reported to the caller, which must restart instead of publishing partial data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import connection
from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_datetime

PREVIEW_CURSOR_MAX_AGE_SECONDS = settings.SIMULATION_PREVIEW_CURSOR_MAX_AGE_SECONDS
PREVIEW_CURSOR_VERSION = 2
PREVIEW_CURSOR_SALT = "simulate.preview-pagination.v2"


class PreviewCursorInvalid(ValueError):
    """The continuation is malformed, expired, or belongs to another list."""


class PreviewSnapshotChanged(RuntimeError):
    """Rows in the cursor-bound snapshot changed between page requests."""


class PreviewSnapshotUnavailable(RuntimeError):
    """The database cannot prove the requested snapshot exactly."""


@dataclass(frozen=True)
class PreviewRevision:
    snapshot: str
    physical_total: int
    active_total: int


@dataclass(frozen=True)
class PreviewCursorState:
    kind: str
    parent_id: str
    scope_id: str | None
    snapshot_at: datetime
    revision: PreviewRevision
    page_size: int
    emitted: int = 0
    after_created_at: datetime | None = None
    after_id: UUID | None = None


def encode_preview_cursor(state: PreviewCursorState) -> str:
    payload = {
        "v": PREVIEW_CURSOR_VERSION,
        "kind": state.kind,
        "parent_id": state.parent_id,
        "scope_id": state.scope_id,
        "snapshot_at": state.snapshot_at.isoformat(),
        "snapshot": state.revision.snapshot,
        "physical_total": state.revision.physical_total,
        "snapshot_total": state.revision.active_total,
        "page_size": state.page_size,
        "emitted": state.emitted,
        "after_created_at": (
            state.after_created_at.isoformat() if state.after_created_at else None
        ),
        "after_id": str(state.after_id) if state.after_id else None,
    }
    return signing.dumps(payload, salt=PREVIEW_CURSOR_SALT, compress=True)


def decode_preview_cursor(
    token: str,
    *,
    expected_kind: str,
    expected_parent_id: str,
    expected_scope_id: str | None,
    expected_page_size: int,
) -> PreviewCursorState:
    try:
        payload = signing.loads(
            token,
            salt=PREVIEW_CURSOR_SALT,
            max_age=PREVIEW_CURSOR_MAX_AGE_SECONDS,
        )
        if not isinstance(payload, dict) or payload.get("v") != PREVIEW_CURSOR_VERSION:
            raise PreviewCursorInvalid("Unsupported simulation preview cursor.")
        if payload.get("kind") != expected_kind:
            raise PreviewCursorInvalid("Cursor belongs to another preview list.")
        if str(payload.get("parent_id")) != str(expected_parent_id):
            raise PreviewCursorInvalid("Cursor belongs to another simulation.")
        normalized_scope_id = str(expected_scope_id) if expected_scope_id else None
        if payload.get("scope_id") != normalized_scope_id:
            raise PreviewCursorInvalid("Cursor belongs to another run test.")
        if payload.get("page_size") != expected_page_size:
            raise PreviewCursorInvalid("Page size cannot change during pagination.")

        snapshot_at = parse_datetime(str(payload.get("snapshot_at") or ""))
        after_created_at_raw = payload.get("after_created_at")
        after_created_at = (
            parse_datetime(str(after_created_at_raw)) if after_created_at_raw else None
        )
        after_id_raw = payload.get("after_id")
        after_id = UUID(str(after_id_raw)) if after_id_raw else None
        snapshot = payload.get("snapshot")
        physical_total = int(payload.get("physical_total"))
        snapshot_total = int(payload.get("snapshot_total"))
        emitted = int(payload.get("emitted", 0))
        if (
            snapshot_at is None
            or not isinstance(snapshot, str)
            or not snapshot
            or physical_total < 0
            or snapshot_total < 0
            or emitted < 0
            or emitted > snapshot_total
            or bool(after_created_at) != bool(after_id)
            or (emitted > 0 and after_created_at is None)
        ):
            raise PreviewCursorInvalid("Invalid simulation preview cursor state.")
    except PreviewCursorInvalid:
        raise
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        TypeError,
        ValueError,
    ) as exc:
        raise PreviewCursorInvalid(
            "Invalid or expired simulation preview cursor."
        ) from exc

    return PreviewCursorState(
        kind=expected_kind,
        parent_id=str(expected_parent_id),
        scope_id=str(expected_scope_id) if expected_scope_id else None,
        snapshot_at=snapshot_at,
        revision=PreviewRevision(
            snapshot=snapshot,
            physical_total=physical_total,
            active_total=snapshot_total,
        ),
        page_size=expected_page_size,
        emitted=emitted,
        after_created_at=after_created_at,
        after_id=after_id,
    )


_REVISION_TABLES = {
    "run_test_executions": (
        "simulate_run_test",
        "simulate_test_execution",
        "run_test_id",
    ),
    "test_execution_calls": (
        "simulate_test_execution",
        "simulate_call_execution",
        "test_execution_id",
    ),
}


def _read_revision_state(
    *,
    kind: str,
    parent_id: str,
    original_snapshot: str,
) -> tuple[Any, ...]:
    """Read current row-version visibility and counts in one SQL statement."""

    try:
        parent_table, child_table, child_fk = _REVISION_TABLES[kind]
    except KeyError as exc:
        raise PreviewSnapshotUnavailable("Unsupported preview snapshot kind.") from exc

    # Identifiers come exclusively from the constant mapping above. Request data
    # remains parameterized. Include deleted physical rows so an equal-count
    # soft-delete/insert swap cannot evade the fence.
    # ``xmin`` is stored as a wrapping 32-bit xid, while ``txid_snapshot``
    # contains epoch-extended 64-bit ids. Reconstruct the current tuple xid in
    # the current epoch before checking visibility; a direct xmin::bigint cast
    # would misclassify new tuples after the cluster's first XID wraparound.
    sql = f"""
        WITH xid_clock AS MATERIALIZED (
          SELECT txid_current()::bigint AS current_txid
        )
        SELECT
          COALESCE((
            SELECT txid_visible_in_snapshot(
              xid_clock.current_txid - age(parent_item.xmin),
              %s::txid_snapshot
            )
            FROM {parent_table} AS parent_item
            CROSS JOIN xid_clock
            WHERE parent_item.id = %s
          ), FALSE) AS parent_visible,
          child_state.physical_count,
          child_state.active_count,
          child_state.all_visible
        FROM (
          SELECT
            COUNT(*)::bigint AS physical_count,
            COUNT(*) FILTER (
              WHERE NOT child_item.deleted
            )::bigint AS active_count,
            COALESCE(BOOL_AND(txid_visible_in_snapshot(
              xid_clock.current_txid - age(child_item.xmin),
              %s::txid_snapshot
            )), TRUE) AS all_visible
          FROM {child_table} AS child_item
          CROSS JOIN xid_clock
          WHERE child_item.{child_fk} = %s
        ) AS child_state
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                original_snapshot,
                parent_id,
                original_snapshot,
                parent_id,
            ],
        )
        state = cursor.fetchone()
    if not state:
        raise PreviewSnapshotUnavailable(
            "The simulation preview revision could not be read."
        )
    return state


def capture_preview_revision(
    *, kind: str, parent_id: str, snapshot: str
) -> PreviewRevision:
    parent_visible, physical_total, active_total, all_visible = _read_revision_state(
        kind=kind,
        parent_id=parent_id,
        original_snapshot=snapshot,
    )
    if not parent_visible or not all_visible:
        raise PreviewSnapshotUnavailable(
            "The simulation preview was not stable inside the read transaction."
        )
    return PreviewRevision(
        snapshot=snapshot,
        physical_total=int(physical_total),
        active_total=int(active_total),
    )


def assert_preview_revision(
    *,
    kind: str,
    parent_id: str,
    revision: PreviewRevision,
) -> None:
    parent_visible, physical_total, active_total, all_visible = _read_revision_state(
        kind=kind,
        parent_id=parent_id,
        original_snapshot=revision.snapshot,
    )
    if not (
        parent_visible
        and all_visible
        and int(physical_total) == revision.physical_total
        and int(active_total) == revision.active_total
    ):
        raise PreviewSnapshotChanged(
            "Simulation preview data changed while more rows were loading."
        )


def paginate_preview_snapshot(
    queryset: QuerySet,
    *,
    kind: str,
    parent_id: str,
    scope_id: str | None,
    page_size: int,
    snapshot_at: datetime,
    snapshot: str,
    cursor: str | None,
    fields: tuple[str, ...],
    before_query: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Return one exact keyset page for a cursor-bound simulation snapshot.

    The caller owns the repeatable-read transaction.  ``created_at`` and the
    UUID primary key form the immutable order. PostgreSQL ``xmin`` visibility
    plus physical/active counts form the revision fence, including bulk SQL
    updates that bypass ``auto_now``. We deliberately fail closed on any
    update/delete/restore/backdated insert affecting the initial membership.
    """

    if cursor:
        state = decode_preview_cursor(
            cursor,
            expected_kind=kind,
            expected_parent_id=parent_id,
            expected_scope_id=scope_id,
            expected_page_size=page_size,
        )
        snapshot_at = state.snapshot_at
    else:
        state = None

    if before_query:
        before_query()
    if state is None:
        revision = capture_preview_revision(
            kind=kind,
            parent_id=parent_id,
            snapshot=snapshot,
        )
        state = PreviewCursorState(
            kind=kind,
            parent_id=str(parent_id),
            scope_id=str(scope_id) if scope_id else None,
            snapshot_at=snapshot_at,
            revision=revision,
            page_size=page_size,
        )
    else:
        assert_preview_revision(
            kind=kind,
            parent_id=parent_id,
            revision=state.revision,
        )

    # Membership is frozen by the signed PostgreSQL MVCC revision above. A
    # wall-clock ``created_at <= transaction timestamp`` fence is both
    # redundant and unsafe: application/database clock skew can otherwise
    # hide already-committed rows from an "exact" first page.
    page_queryset = queryset
    if state.after_created_at is not None and state.after_id is not None:
        page_queryset = page_queryset.filter(
            Q(created_at__lt=state.after_created_at)
            | Q(created_at=state.after_created_at, id__lt=state.after_id)
        )

    if before_query:
        before_query()
    raw_rows = list(
        page_queryset.order_by("-created_at", "-id").values(*fields)[: page_size + 1]
    )
    has_more = len(raw_rows) > page_size
    rows = raw_rows[:page_size]
    loaded_through = state.emitted + len(rows)

    if loaded_through > state.revision.active_total:
        raise PreviewSnapshotChanged("Simulation preview snapshot count drifted.")
    if not has_more and loaded_through != state.revision.active_total:
        raise PreviewSnapshotChanged("Simulation preview snapshot is incomplete.")
    if has_more and (not rows or loaded_through >= state.revision.active_total):
        raise PreviewSnapshotChanged("Simulation preview continuation is inconsistent.")

    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = encode_preview_cursor(
            PreviewCursorState(
                kind=kind,
                parent_id=str(parent_id),
                scope_id=str(scope_id) if scope_id else None,
                snapshot_at=state.snapshot_at,
                revision=state.revision,
                page_size=page_size,
                emitted=loaded_through,
                after_created_at=last["created_at"],
                after_id=last["id"],
            )
        )

    return {
        "results": rows,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "snapshot_total": state.revision.active_total,
        "loaded_through": loaded_through,
        "complete": not has_more,
        "exact": True,
        "snapshot_at": state.snapshot_at,
    }

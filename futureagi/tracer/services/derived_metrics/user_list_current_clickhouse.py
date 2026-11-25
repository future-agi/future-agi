"""Users activity derived from the SAME frozen member publication, never raw SQL.

BL01-Q owns complete discovery, bounded freezing, source/remap fences and the
qualification of CurrentFactsRead. This module grants none of those guarantees.
A receipt for schema 036 alone does not qualify schema 037's coverage.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import (
    LIST_CURRENT_MEMBERS,
    LIST_USER_ACTIVITY,
)

if TYPE_CHECKING:
    from tracer.models.derived_metric import MetricReadManifest, MetricRefreshAttempt
    from tracer.services.derived_metrics.base_list_current import CurrentFactsRead
    from tracer.services.derived_metrics.bounded_reads import KeyRelation
    from tracer.services.derived_metrics.publication import FrozenRows
    from tracer.services.derived_metrics.refresh import RefreshWriter


def freeze_user_activity(
    writer: RefreshWriter,
    attempt: MetricRefreshAttempt,
    head: MetricReadManifest | None,
    *,
    member_payloads: Sequence[FrozenRows],
    keys: Sequence[tuple[str, str]],
) -> None:
    """Retract old user keys and copy frozen current members in one attempt.

    A correction, deletion, disappeared physical identity or remap-only update
    must replace both sides of a user-key move. Never group by a request window
    or compute persisted per-user totals. The parent's member batch already
    contains tombstones and retention corrections; do not re-read native spans.
    """
    from tracer.services.derived_metrics.refresh import payload_select

    frozen, bound = payload_select(
        LIST_CURRENT_MEMBERS, member_payloads, prefix="user_activity_members"
    )
    previous, previous_params = committed_rows_select(
        LIST_USER_ACTIVITY,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        key_in={("trace_id", "span_id"): keys},
        param_prefix="user_activity_previous",
    )
    columns = ", ".join(
        column
        for column in LIST_USER_ACTIVITY.columns
        if column not in {"org_id", "generation", "attempt_id", "_version"}
    )
    # The user is intentionally in this logical key. All removed keys get a
    # tombstone even when the identity still exists under another user.
    key = ", ".join(LIST_USER_ACTIVITY.logical_key)
    sql = f"""SELECT {columns} FROM ({frozen})
        UNION ALL
        SELECT {columns} FROM (
            SELECT * REPLACE(toUInt8(1) AS is_deleted) FROM ({previous})
            WHERE ({key}) NOT IN (SELECT {key} FROM ({frozen}))
        )"""
    writer.freeze(attempt, LIST_USER_ACTIVITY, sql, {**bound, **previous_params})


def activity_rows(
    read: CurrentFactsRead,
    *,
    key_relation: KeyRelation | None = None,
    param_prefix: str = "user_activity",
) -> tuple[str, dict[str, Any]]:
    """Read the shared pinned allowlist, collapsing before mutable predicates."""
    if (
        not read.active
        or not read.permits(read.manifest.project_id)
        or not read.permits_user_activity(read.manifest.project_id)
    ):
        raise ValueError("user activity requires an active qualified current-fact pin")
    sql, params = committed_rows_select(
        LIST_USER_ACTIVITY,
        read.manifest,
        org_id=read.org_id,
        project_id=read.manifest.project_id,
        key_relation=key_relation,
        param_prefix=param_prefix,
    )
    return sql, params

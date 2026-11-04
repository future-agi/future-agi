"""Bounded discovery of new and late-committing Datadog source rows."""

from datetime import datetime
from typing import TypedDict
from uuid import UUID

from django.db.models import BooleanField, QuerySet
from django.db.models.expressions import RawSQL

from agentcc.models import AgentccRequestLog
from integrations.models import DatadogExportReceipt, IntegrationConnection

# At most two indexed pages per poll, independent of the retained history size.
SCAN_PAGE_SIZE = 5000


class Position(TypedDict):
    created_at: str
    id: str


class ScanProgress(TypedDict, total=False):
    new_after: Position
    reconcile_after: Position
    reconcile_until: Position


def _position(row: tuple[UUID, datetime]) -> Position:
    return {"id": str(row[0]), "created_at": row[1].isoformat()}


def _after(source: QuerySet, position: Position | None) -> QuerySet:
    if not position:
        return source
    # A row comparison gives PostgreSQL one contiguous range in the scan index;
    # the equivalent timestamp/UUID OR can re-scan a large equal-timestamp prefix.
    return source.filter(
        RawSQL(
            "(created_at, id) > (%s, %s::uuid)",
            [datetime.fromisoformat(position["created_at"]), position["id"]],
            output_field=BooleanField(),
        )
    )


def snapshot_request_ids(
    connection: IntegrationConnection, floor: datetime, sync_to: datetime
) -> tuple[list[UUID], ScanProgress]:
    """Read one forward page and one historical reconciliation page on primary.

    A timestamp cannot prove transaction visibility. A fixed-bound round-robin
    sweep therefore revisits old keys forever, finding arbitrarily late commits
    without a full-history anti-join on every minute's poll. Its position commits
    atomically with the receipts; failures cannot advance discovery past a row.
    """
    source = (
        AgentccRequestLog.no_workspace_objects.using("default")
        .filter(
            organization_id=connection.organization_id,
            workspace_id=connection.workspace_id,
            deleted=False,
            created_at__gte=floor,
            created_at__lt=sync_to,
        )
        .order_by("created_at", "id")
    )
    previous: ScanProgress = connection.sync_cursor.get("datadog_scan", {})
    progress: ScanProgress = dict(previous)
    fresh = list(
        _after(source, previous.get("new_after")).values_list("id", "created_at")[
            :SCAN_PAGE_SIZE
        ]
    )
    if fresh:
        progress["new_after"] = _position(fresh[-1])

    # Freeze each sweep's upper bound so continuous arrivals cannot starve the
    # wraparound that discovers a late commit behind the current scan position.
    bound = previous.get("reconcile_until") or previous.get("new_after")
    history = []
    if bound:
        history_source = source.filter(
            RawSQL(
                "(created_at, id) <= (%s, %s::uuid)",
                [datetime.fromisoformat(bound["created_at"]), bound["id"]],
                output_field=BooleanField(),
            )
        )
        history = list(
            _after(history_source, previous.get("reconcile_after")).values_list(
                "id", "created_at"
            )[:SCAN_PAGE_SIZE]
        )
        if len(history) == SCAN_PAGE_SIZE and _position(history[-1]) != bound:
            progress["reconcile_after"] = _position(history[-1])
            progress["reconcile_until"] = bound
        else:
            progress.pop("reconcile_after", None)
            progress.pop("reconcile_until", None)

    candidates = {row[0] for row in fresh + history}
    delivered = set(
        DatadogExportReceipt.objects.using("default")
        .filter(connection_id=connection.pk, request_log_id__in=candidates)
        .values_list("request_log_id", flat=True)
    )
    return sorted(candidates - delivered), progress

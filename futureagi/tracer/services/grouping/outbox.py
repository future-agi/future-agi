"""Metadata-only, at-least-once wake outbox for the Node Kafka producer."""

import uuid

from django.db import transaction
from django.utils import timezone

from tracer.models.trace_grouping import TraceGroupingOutbox
from tracer.services.grouping.control import GroupingControlError, GroupingNotFound


def list_grouping_outbox(*, limit: int = 100) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise GroupingControlError("outbox limit must be between 1 and 100")
    rows = TraceGroupingOutbox.no_workspace_objects.filter(
        delivered_at__isnull=True,
    ).order_by("created_at", "id")[:limit]
    return {
        "events": [
            {
                "id": str(row.id),
                "event_kind": row.event_kind,
                "source_id": str(row.source_id),
                "revision": row.revision,
                "scope_id": str(row.scope_id),
            }
            for row in rows
        ]
    }


def acknowledge_grouping_outbox(*, event_id: uuid.UUID) -> dict:
    """Call only after broker ACK. Duplicate ACKs return the original time."""
    with transaction.atomic():
        row = (
            TraceGroupingOutbox.no_workspace_objects.select_for_update()
            .filter(pk=event_id)
            .first()
        )
        if row is None:
            raise GroupingNotFound("outbox event was not found")
        if row.delivered_at is None:
            row.delivered_at = timezone.now()
            row.attempts += 1
            row.save(update_fields=["delivered_at", "attempts", "updated_at"])
        return {"id": str(row.id), "delivered_at": row.delivered_at}

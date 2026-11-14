"""Attachment fences for the existing PG and collector-session delete paths.

The durable PG deletion marker wins over a stale/live ClickHouse span. Reads
check it synchronously; cleanup retries bounded batches until bytes are erased.
No PG Trace is synthesized for collector data.
"""

from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from tracer.models.attachment import AttachmentOccurrence, AttachmentScope
from tracer.models.trace_session import TraceSession, TraceSessionOverlay

BATCH = 100


def deleted_sessions(project_id, session_ids=None):
    filters = {"project_id": project_id, "deleted": True}
    overlays = TraceSessionOverlay._base_manager.using("default").filter(**filters)
    sessions = TraceSession._base_manager.using("default").filter(**filters)
    if session_ids is not None:
        overlays = overlays.filter(trace_session_id__in=session_ids)
        sessions = sessions.filter(id__in=session_ids)
    return overlays, sessions


def deleted_session_ids(project_id, session_ids, reader):
    """Honor the same bounded remap groups as the product session reader."""
    ids = {str(UUID(str(value))) for value in session_ids if value}
    if not ids:
        return set()
    groups = reader.remap_groups("trace_session_id_remap", ids)
    overlays, sessions = deleted_sessions(project_id, ids | set(groups))
    deleted = {
        str(value) for value in overlays.values_list("trace_session_id", flat=True)
    } | {str(value) for value in sessions.values_list("id", flat=True)}
    survivors = {groups.get(value, value) for value in deleted}
    return {value for value in ids if groups.get(value, value) in survivors}


def session_deleted(project_id, session_id):
    if not session_id:
        return False
    try:
        key = UUID(str(session_id))
    except (ValueError, TypeError):
        return True  # Unknown source identity cannot authorize media.
    overlays, sessions = deleted_sessions(project_id, [key])
    if overlays.exists() or sessions.exists():
        return True
    overlays, sessions = deleted_sessions(project_id)
    if not overlays.exists() and not sessions.exists():
        return False
    from tracer.services.clickhouse.v2 import get_reader

    # A marker may use a pre-cutover/survivor id while the span holds a new id.
    # A remap read failure propagates to LiveAuthority's fail-closed boundary.
    return bool(deleted_session_ids(project_id, [key], get_reader()))


def sweep_deleted_sessions(*, project_id):
    """One fair, bounded pass, also called immediately after a session delete.

    The session marker survives a CH outage, and LiveAuthority denies reads
    independently of this cleanup. Only exact project/trace/span membership
    read from CH can select an occurrence for erasure. A failed CH read leaves
    the batch unmodified for the next scheduled sweep.
    """
    from tracer.services.attachments.retention import _tombstone
    from tracer.services.attachments.uploads import lock_all_budgets
    from tracer.services.clickhouse.v2 import get_reader

    overlays, sessions = deleted_sessions(project_id)
    if not overlays.exists() and not sessions.exists():
        return 0
    scope = AttachmentScope.objects.filter(project_id=project_id).first()
    if scope is None:
        return 0
    rows = list(
        AttachmentOccurrence.objects.filter(scope=scope, deleted_at__isnull=True)
        .order_by(F("source_checked_at").asc(nulls_first=True), "id")[:BATCH]
    )
    if not rows:
        return 0
    reader = get_reader()
    spans = reader.list_by_ids(
        [row.span_id for row in rows],
        include_heavy=False,
        project_id=str(project_id),
        columns=["id", "trace_id", "trace_session_id"],
    )
    parents = {
        (str(span["trace_id"]), str(span["id"])): span.get("trace_session_id")
        for span in spans
    }
    session_ids = {value for value in parents.values() if value}
    deleted = deleted_session_ids(project_id, session_ids, reader)
    selected = [
        row.pk
        for row in rows
        if str(parents.get((str(row.trace_id), row.span_id))) in deleted
    ]
    with transaction.atomic():
        lock_all_budgets(scope)
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.pk)
        AttachmentOccurrence.objects.filter(
            scope=scope, pk__in=[row.pk for row in rows], deleted_at__isnull=True
        ).update(source_checked_at=timezone.now())
        return sum(
            _tombstone(row, scope, reason="requested")
            for row in AttachmentOccurrence.objects.filter(
                scope=scope, pk__in=selected, deleted_at__isnull=True
            )
        )

"""Scheduled attachment lifecycle: the outbox drain and the sweeps.

The Temporal activities in ``tracer.tasks.attachment_lifecycle`` call these on
the schedules in ``tfc.temporal.schedules.attachment_lifecycle``, which exist
only when ``FI_ATTACHMENTS_ENABLED``. Each unit of work is isolated: a failing
outbox row or scope is logged by reason code, pushed back and never stalls the
rest of the batch. Counts and reason codes only: no identifiers, references or
payloads are logged.

- ``drain_outbox``: every ``AttachmentOutbox.event`` kind has exactly one
  consumer in ``CONSUMERS`` (validate, erase, settled, admitted).
- ``sweep``: expired reservations, expired occurrences, occurrences of fenced
  (deleted) scopes, then the media session / playback grant / audit purge.
- ``sweep_orphans``: bound occurrences whose parent span is gone from ClickHouse
  (TTL, enterprise retention, any delete that bypassed the PostgreSQL hooks),
  only after two misses at least ``orphan_grace()`` apart and never while
  PostgreSQL still holds the trace or span as not deleted.
"""

from collections import defaultdict
from datetime import timedelta
from uuid import UUID

import structlog
from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentMediaSession,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentPlaybackGrant,
    AttachmentScope,
)
from tracer.services.attachments import accounting, retention, validation
from tracer.services.attachments.contract import AttachmentError

logger = structlog.get_logger(__name__)

# Per-tick bounds. A validate job runs one sandbox probe, so it is the slow one.
DRAIN_LIMITS = {"validate": 8, "erase": 16, "settled": 100, "admitted": 100}
# A row whose consumer raised, or that is not finished yet (an erase whose
# attachment still has a live occurrence), waits this long before it is due again.
RETRY_SECONDS = 60
NOT_YET_SECONDS = 300
# Media sessions and playback grants are kept this long past their expiry.
GRANT_GRACE = timedelta(hours=1)
PURGE_LIMIT = 1000
ORPHAN_LIMIT = 200


def storage_from_settings():
    """The deployment's private media storage; raises AttachmentError if unset."""
    from tracer.services.attachments.storage import from_settings

    return from_settings(settings)


def _consume_validate(pk, storage):
    # Claims, leases and backs off on its own; settles the upload when done.
    return validation.consume_validation(pk, storage=storage)


def _consume_erase(pk, storage):
    return retention.erase(pk, storage=storage)


def _consume_settled(pk, storage):
    accounting.consume_settlement(pk)
    return True


def _consume_admitted(pk, storage):
    return accounting.consume_admission(pk)


CONSUMERS = {
    "validate": _consume_validate,
    "erase": _consume_erase,
    "settled": _consume_settled,
    "admitted": _consume_admitted,
}
NEEDS_STORAGE = frozenset({"validate", "erase"})


def _push_back(pk, seconds):
    AttachmentOutbox.objects.filter(pk=pk, processed_at__isnull=True).update(
        available_at=timezone.now() + timedelta(seconds=seconds)
    )


def _due(event, limit):
    return list(
        AttachmentOutbox.objects.filter(
            event=event, processed_at__isnull=True, available_at__lte=timezone.now()
        )
        .order_by("available_at", "id")
        .values_list("pk", flat=True)[:limit]
    )


def drain_outbox(*, storage=None, limits=None):
    """Run every due outbox row once, per kind, within ``limits``. Returns counts."""
    limits = {**DRAIN_LIMITS, **(limits or {})}
    counts = defaultdict(int)
    storage_error = None
    for event, consumer in CONSUMERS.items():
        if event in NEEDS_STORAGE and storage is None and storage_error is None:
            try:
                storage = storage_from_settings()
            except AttachmentError as exc:
                storage_error = exc.code
        if event in NEEDS_STORAGE and storage is None:
            # Never validate or erase without the real storage: rows stay queued.
            counts[f"{event}_skipped_{storage_error}"] += 1
            continue
        for pk in _due(event, limits[event]):
            try:
                finished = consumer(pk, storage)
            except AttachmentError as exc:
                counts[f"{event}_error_{exc.code}"] += 1
                _push_back(pk, RETRY_SECONDS)
                continue
            except Exception:  # noqa: BLE001 - per-row isolation; one row never stalls the drain
                logger.exception("attachment_outbox_row_failed", kind=event)
                counts[f"{event}_error"] += 1
                _push_back(pk, RETRY_SECONDS)
                continue
            if finished:
                counts[f"{event}_done"] += 1
            elif event != "validate":
                # validate keeps its own lease/backoff on the row.
                counts[f"{event}_not_yet"] += 1
                _push_back(pk, NOT_YET_SECONDS)
            else:
                counts[f"{event}_retrying"] += 1
    return dict(counts)


def _isolated(counts, name, func, **kwargs):
    try:
        counts[name] += func(**kwargs) or 0
    except AttachmentError as exc:
        counts[f"{name}_error_{exc.code}"] += 1
    except Exception:  # noqa: BLE001 - per-scope isolation; one scope never stalls the sweep
        logger.exception("attachment_sweep_step_failed", step=name)
        counts[f"{name}_error"] += 1


def purge_media(*, now=None, limit=PURGE_LIMIT):
    """Delete expired or revoked media sessions (their grants cascade) and
    expired grants of live sessions. Both are past use once expired."""
    cutoff = (now or timezone.now()) - GRANT_GRACE
    grants = list(
        AttachmentPlaybackGrant.objects.filter(expires_at__lt=cutoff).values_list(
            "pk", flat=True
        )[:limit]
    )
    deleted = AttachmentPlaybackGrant.objects.filter(pk__in=grants).delete()[0]
    sessions = list(
        (
            AttachmentMediaSession.objects.filter(expires_at__lt=cutoff)
            | AttachmentMediaSession.objects.filter(revoked_at__lt=cutoff)
        ).values_list("pk", flat=True)[:limit]
    )
    with transaction.atomic():
        deleted += AttachmentMediaSession.objects.filter(pk__in=sessions).delete()[0]
    return deleted


def audit_retention():
    days = int(getattr(settings, "FI_ATTACHMENTS_AUDIT_RETENTION_DAYS", 400))
    return timedelta(days=max(days, 1))


def sweep(*, now=None):
    """One bounded pass of every time-based cleanup. Returns counts."""
    now = now or timezone.now()
    counts = defaultdict(int)
    _isolated(counts, "reservations_expired", accounting.expire_reservations)
    for scope_id, enabled in AttachmentScope.objects.values_list("pk", "enabled"):
        _isolated(
            counts,
            "occurrences_expired",
            retention.expire_occurrences,
            scope_id=scope_id,
        )
        if not enabled:
            # fence_scope disables the scope and bumps its generation; this is
            # the physical cleanup that follows a project/workspace deletion.
            _isolated(
                counts,
                "occurrences_fenced",
                retention.tombstone_fenced,
                scope_id=scope_id,
            )
    _isolated(counts, "media_purged", purge_media, now=now)
    _isolated(
        counts, "audit_purged", retention.purge_audit, before=now - audit_retention()
    )
    return dict(counts)


def clickhouse_spans(project_id, span_ids):
    """``{span_id: trace_id}`` of the live spans among ``span_ids``.

    ``spans FINAL`` drops deleted rows, so a missing id means the span is gone.
    Lean projection: fat voice spans never enter the merge.
    """
    from tracer.services.clickhouse.v2 import get_reader

    rows = get_reader().list_by_ids(
        list(span_ids),
        include_heavy=False,
        project_id=str(project_id),
        columns=["id", "trace_id"],
    )
    return {str(row["id"]): str(row["trace_id"]) for row in rows}


def _same_trace(value, trace_id):
    try:
        return UUID(str(value)) == trace_id
    except (TypeError, ValueError):
        return False


def orphan_grace():
    """How long a parent span must stay missing before its media is erased.

    The same window skips freshly bound occurrences: ClickHouse is eventually
    consistent, so one empty read is never proof that a span is gone.
    """
    hours = int(getattr(settings, "FI_ATTACHMENTS_ORPHAN_GRACE_HOURS", 24))
    return timedelta(hours=max(hours, 1))


def postgres_live_parents(project_id, rows):
    """``(trace_ids, span_ids)`` that PostgreSQL still holds as not deleted.

    Ingest writes a PostgreSQL Trace (and, on the legacy path, an
    ObservationSpan) next to the ClickHouse span, and the product's deletion
    paths soft-delete them. A live row here means the span was not deleted, so
    a ClickHouse miss is lag or a partial replica, not an orphan. An absent row
    (collector-only ingest, or the legacy span table dropped) proves nothing
    either way and leaves the decision to the two ClickHouse misses.
    """
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace

    traces = set(
        Trace.no_workspace_objects.filter(
            project_id=project_id, id__in={row["trace_id"] for row in rows}
        ).values_list("id", flat=True)
    )
    spans = set()
    if ObservationSpan._meta.db_table in connection.introspection.table_names():
        spans = set(
            ObservationSpan.no_workspace_objects.filter(
                project_id=project_id, id__in={row["span_id"] for row in rows}
            ).values_list("id", flat=True)
        )
    return traces, spans


def sweep_orphans(
    *,
    span_reader=clickhouse_spans,
    pg_reader=postgres_live_parents,
    limit=ORPHAN_LIMIT,
    now=None,
):
    """Tombstone bound occurrences whose parent span is gone, after two misses.

    A tombstone is irreversible (0133) and queues the erase at once, so one
    read of an eventually consistent ClickHouse never decides it:

    - occurrences bound within ``orphan_grace()`` are not checked at all;
    - a read error skips that project untouched;
    - a span ClickHouse returns in the same trace, or one PostgreSQL still holds
      as not deleted, clears ``source_missing_since``;
    - the first miss only stamps ``source_missing_since``; a later miss at least
      ``orphan_grace()`` after it tombstones. A live read in between restarts
      the clock.

    Every checked row is stamped, so each tick moves on to the least recently
    checked ones.
    """
    now = now or timezone.now()
    grace = orphan_grace()
    rows = list(
        AttachmentOccurrence.objects.filter(
            deleted_at__isnull=True,
            bound_at__isnull=False,
            bound_at__lte=now - grace,
        )
        .order_by(F("source_checked_at").asc(nulls_first=True), "id")
        .values(
            "pk",
            "scope_id",
            "scope__project_id",
            "trace_id",
            "span_id",
            "source_missing_since",
        )[:limit]
    )
    by_project = defaultdict(list)
    for row in rows:
        by_project[(row["scope_id"], row["scope__project_id"])].append(row)
    counts = defaultdict(int)
    for (scope_id, project_id), group in by_project.items():
        try:
            live = span_reader(project_id, {row["span_id"] for row in group})
        except Exception:  # noqa: BLE001 - ClickHouse boundary; never tombstone on a failed read
            logger.warning("attachment_orphan_read_failed")
            counts["read_failed"] += 1
            continue
        missing = [
            row
            for row in group
            if not _same_trace(live.get(row["span_id"]), row["trace_id"])
        ]
        missing_pks = {row["pk"] for row in missing}
        pg_traces, pg_spans = set(), set()
        if missing:
            try:
                with transaction.atomic():
                    pg_traces, pg_spans = pg_reader(project_id, missing)
            except Exception:  # noqa: BLE001 - never tombstone without the PostgreSQL check
                logger.warning("attachment_orphan_pg_read_failed")
                counts["pg_read_failed"] += 1
                continue
        seen, first, pending, gone = [], [], [], []
        for row in group:
            if row["pk"] not in missing_pks:
                seen.append(row["pk"])
            elif row["trace_id"] in pg_traces or row["span_id"] in pg_spans:
                counts["kept_pg_live"] += 1
                seen.append(row["pk"])
            elif row["source_missing_since"] is None:
                first.append(row["pk"])
            elif row["source_missing_since"] <= now - grace:
                gone.append(row["pk"])
            else:
                pending.append(row["pk"])
        AttachmentOccurrence.objects.filter(pk__in=seen).update(
            source_checked_at=now, source_missing_since=None
        )
        AttachmentOccurrence.objects.filter(pk__in=first).update(
            source_checked_at=now, source_missing_since=now
        )
        AttachmentOccurrence.objects.filter(pk__in=pending).update(
            source_checked_at=now
        )
        counts["checked"] += len(seen)
        counts["missing_first"] += len(first)
        counts["missing_pending"] += len(pending)
        if gone:
            _isolated(
                counts,
                "orphans_tombstoned",
                retention.tombstone_occurrences,
                scope_id=scope_id,
                occurrence_ids=gone,
                missing_before=now - grace,
            )
    return {name: value for name, value in counts.items() if value}

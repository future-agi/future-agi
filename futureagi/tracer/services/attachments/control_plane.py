"""Concrete ingest adapters over the attachment control plane.

- ``OccurrenceBindingStore``: the durable pending set is the unbound live
  ``AttachmentOccurrence`` rows themselves (B01). Claims are round-robin across
  scopes, least recently checked first, with a per-scope cap per tick; a row
  that stays pending is stamped (``bind_checked_at``) and goes to the back.
  Each scope's window is bounded on its index before it is numbered, so a
  claim never sorts the whole backlog.
  Binding goes through ``retention.bind_retention`` (fenced CAS, B05/B09);
  expiry, rejection and permanent bind fences (stale policy or deletion
  generation) tombstone through ``retention._tombstone``, which enqueues the
  erase outbox.
- ``ClickHouseParentReader``: one ``spans FINAL`` batch per project per tick.
  ``source_state`` derives the stored source facts with the same rule as
  ``authority.LiveAuthority.source``, so binding and reading cannot disagree.
- ``ControlPlaneReader``: the B10 reader installed on trace and Voice reads.
  Per scope it checks the live application session once, loads the rows with
  their attachment, session and recording in one query, reads their spans in one
  ``spans FINAL`` batch, and re-authorizes every row in memory with
  ``occurrences.check_readable`` (the ``get_occurrence`` gate); with
  ``FI_ATTACHMENTS_ENABLED`` off it does no I/O.

No adapter fetches media, follows a URL or trusts an OTLP timestamp.
"""

from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBlob,
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentState,
)
from tracer.models.project import Project
from tracer.services.attachments import manifests, occurrences, principal, retention
from tracer.services.attachments.authority import (
    SOURCE_GENERATION,
    LiveAuthority,
    _aware,
    retention_for,
)
from tracer.services.attachments.binding import (
    MAX_BATCH,
    Parent,
    ParentState,
    PendingLink,
    Scope,
    reconcile_pending,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.reads import MAX_PER_PARENT, AuthorizedStatus
from tracer.services.attachments.references import (
    REFS,
    InvalidReference,
    parse_references,
)
from tracer.services.attachments.uploads import _lock_budgets

SPAN_COLUMNS = ["id", "trace_id", "project_id", "start_time", "is_deleted"]
# A worker tick walks at most this many claim batches before yielding.
MAX_TICK_BATCHES = 8
# One scope gets at most this many claims per tick, so a backlog in one
# project never consumes a whole tick while another project waits.
PER_SCOPE_TICK_CAP = 512
# One trace or Voice read authorizes at most this many occurrences in total.
MAX_READ_ROWS = MAX_PER_PARENT
# The binder reads parents whose span started within this window around the
# uploads it claims. The parent span normally starts before its upload; the
# lookback covers long calls and the slack covers SDK clock skew. A span that
# started outside it reads as absent: the row stays pending and expires after
# ``binding.MAX_PENDING_AGE`` like any parent that never arrives.
BIND_LOOKBACK = timedelta(hours=48)
BIND_CLOCK_SLACK = timedelta(hours=1)


def read_spans(
    project_id, span_ids, *, with_attributes=False, trace_ids=(), started=()
):
    """``{span_id: span}`` from one ``spans FINAL`` read for one project.

    ``trace_ids`` keys the batch by (project, trace); ``started`` is an
    optional ``(earliest, latest)`` start-time pair that bounds the read to the
    partitions and hour buckets covering it (see ``list_by_ids``).
    """
    from tracer.services.clickhouse.v2 import get_reader

    columns = SPAN_COLUMNS + (["attrs_string"] if with_attributes else [])
    rows = get_reader().list_by_ids(
        sorted(span_ids),
        project_id=str(project_id),
        columns=columns,
        trace_ids=sorted(str(trace_id) for trace_id in trace_ids) or None,
        start_window=tuple(started) or None,
    )
    return {str(row["id"]): SimpleNamespace(**row) for row in rows}


def source_state(scope, parent, span):
    """The backend ``SourceState`` for a live span, or None.

    Mirrors ``LiveAuthority.source`` field for field (minus the viewer-only
    ``writable``): the bound row must equal what every later read derives.
    """
    if span is None or getattr(span, "is_deleted", 1):
        return None
    try:
        trace = UUID(str(span.trace_id))
        started_at = _aware(span.start_time)
    except (ValueError, TypeError, AttributeError):
        return None
    if (
        str(span.project_id) != str(scope.project_id)
        or str(span.id) != parent.span_id
        or trace != parent.trace_id
        or not isinstance(started_at, datetime)
    ):
        return None
    expires_at = retention_for(scope.organization_id, started_at)
    if expires_at is None:
        return None
    return occurrences.SourceState(
        scope_id=scope.id,
        parent=parent,
        generation=SOURCE_GENERATION,
        started_at=started_at,
        expires_at=expires_at,
        readable=True,
    )


def _references(span):
    """Persisted normalized refs by occurrence id; malformed refs prove nothing."""
    raw = (getattr(span, "attrs_string", None) or {}).get(REFS)
    try:
        refs = parse_references(1, raw)
    except InvalidReference:
        return {}
    return {ref["occurrence_id"]: ref for ref in refs}


def _permanently_fenced(row, scope, now):
    """A bind fence no later tick can pass: retire the row instead of retrying."""
    return (
        row.policy_version != scope.policy_version
        or row.deletion_generation != scope.deletion_generation
        or row.expires_at <= now
    )


# Round-robin claim: every enabled scope's least recently checked live unbound
# rows (``attachment_unbound_claim_idx``: scope, COALESCE(bind_checked_at,
# created_at), id), then every scope's second, and so on. Each scope's window is
# bounded BEFORE it is numbered (LATERAL ... LIMIT over the index), so the cost
# is one short index scan per scope, never a sort of the whole backlog.
# Aborted or erased bytes have nothing left to bind; failed and rejected
# uploads still bind, so readers can show that state.
_CLAIM_SQL = """
SELECT c.id, c.scope_id
FROM tracer_attachmentscope s
LEFT JOIN unnest(%(used_scopes)s::uuid[], %(used_counts)s::int[]) AS u(scope_id, used)
  ON u.scope_id = s.id
CROSS JOIN LATERAL (
  SELECT w.id, w.scope_id, w.touched,
         row_number() OVER (ORDER BY w.touched, w.id) AS turn
  FROM (
    SELECT o.id, o.scope_id, COALESCE(o.bind_checked_at, o.created_at) AS touched
    FROM tracer_attachmentoccurrence o
    JOIN tracer_attachment a ON a.id = o.attachment_id
    WHERE o.scope_id = s.id
      AND o.bound_at IS NULL
      AND o.deleted_at IS NULL
      AND a.state <> ALL(%(dead_states)s::text[])
      AND o.id <> ALL(%(claimed)s::uuid[])
    ORDER BY COALESCE(o.bind_checked_at, o.created_at), o.id
    LIMIT LEAST(%(limit)s, %(cap)s - COALESCE(u.used, 0))
  ) w
) c
WHERE s.enabled AND %(cap)s - COALESCE(u.used, 0) > 0
ORDER BY c.turn, c.touched, c.id
LIMIT %(limit)s
"""


class OccurrenceBindingStore:
    """Pending links are unbound, undeleted occurrences of enabled scopes.

    One store lives for one tick. Within a tick no row is claimed twice; across
    ticks the order is the persisted ``bind_checked_at`` stamp (never-checked
    rows by age), so rows that keep waiting cannot starve newer ones.
    """

    def __init__(self):
        self.claimed = set()
        self.per_scope = Counter()

    def _claim_ids(self, limit):
        """``[(id, scope_id)]`` in claim order, at most ``limit``."""
        used = [
            (scope_id, count) for scope_id, count in self.per_scope.items() if count
        ]
        with connection.cursor() as cursor:
            cursor.execute(
                _CLAIM_SQL,
                {
                    "used_scopes": [str(scope_id) for scope_id, _ in used],
                    "used_counts": [count for _, count in used],
                    "dead_states": [
                        str(AttachmentState.DELETED),
                        str(AttachmentState.EXPIRED),
                    ],
                    "claimed": [str(row_id) for row_id in self.claimed],
                    "limit": limit,
                    "cap": PER_SCOPE_TICK_CAP,
                },
            )
            return cursor.fetchall()

    def claim_pending(self, now, limit):
        ids = self._claim_ids(limit)
        rows = AttachmentOccurrence.objects.select_related("attachment", "scope")
        rows = rows.in_bulk([row_id for row_id, _ in ids])
        links = []
        for row_id, scope_id in ids:
            if self.per_scope[scope_id] >= PER_SCOPE_TICK_CAP:
                continue
            row = rows.get(row_id)
            if row is None:
                continue
            self.claimed.add(row_id)
            self.per_scope[scope_id] += 1
            scope = row.scope
            links.append(
                PendingLink(
                    internal_id=row.id,
                    scope_id=scope.id,
                    scope=Scope(
                        str(scope.organization_id),
                        str(scope.workspace_id),
                        str(scope.project_id),
                    ),
                    parent=Parent(str(row.trace_id), row.span_id),
                    attachment_id=str(row.attachment.client_id),
                    occurrence_id=str(row.client_id),
                    direction=row.direction,
                    modality=row.attachment.modality,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
            )
        return links

    def settle(self, link, state, parent, now):
        if state == "pending":
            self._park(link)
            return True
        if state == "bound":
            try:
                retention.bind_retention(
                    scope_id=link.scope_id,
                    occurrence_id=link.internal_id,
                    source=parent.source,
                )
            except AttachmentError as error:
                if error.code == "binding_fenced" and self._tombstone(
                    link, only_if_fenced=True
                ):
                    return "rejected"
                self._park(link)
                return False
            except (AttachmentOccurrence.DoesNotExist, AttachmentScope.DoesNotExist):
                return False
            return True
        return self._tombstone(link)

    def _park(self, link):
        """Send a still-unbound row to the back of its scope's claim order."""
        AttachmentOccurrence.objects.filter(
            pk=link.internal_id, bound_at__isnull=True, deleted_at__isnull=True
        ).update(bind_checked_at=timezone.now())

    @transaction.atomic
    def _tombstone(self, link, *, only_if_fenced=False):
        # Same lock order as retention.expire_occurrences: budgets, scope, row.
        scope = AttachmentScope.objects.get(pk=link.scope_id)
        _lock_budgets(scope)
        scope = AttachmentScope.objects.select_for_update().get(pk=link.scope_id)
        row = (
            AttachmentOccurrence.objects.select_for_update()
            .filter(
                pk=link.internal_id,
                scope=scope,
                bound_at__isnull=True,
                deleted_at__isnull=True,
            )
            .first()
        )
        if row is None:
            return False
        if only_if_fenced and not _permanently_fenced(row, scope, timezone.now()):
            return False
        # The audit vocabulary is closed; an unbindable occurrence is retired
        # by the same "retention" deletion as an expired bound one.
        return retention._tombstone(row, scope, reason="retention")


class ClickHouseParentReader:
    def __init__(self, span_reader=read_spans):
        self.span_reader = span_reader

    def latest(self, links):
        """One ``spans FINAL`` read per (scope, project), keyed by its traces.

        The read is bounded to the partitions around the claimed uploads'
        creation times (``BIND_LOOKBACK`` before the oldest, ``BIND_CLOCK_SLACK``
        after the newest), so a growing backlog never widens a FINAL merge to
        the whole project.
        """
        groups = {}
        for link in links:
            groups.setdefault((link.scope_id, link.scope.project_id), []).append(link)
        scopes = AttachmentScope.objects.in_bulk([scope_id for scope_id, _ in groups])
        result = {}
        for (scope_id, project_id), group in groups.items():
            scope = scopes.get(scope_id)
            if scope is None:
                continue
            created = [link.created_at for link in group]
            spans = self.span_reader(
                project_id,
                {link.parent.span_id for link in group},
                with_attributes=True,
                trace_ids={link.parent.trace_id for link in group},
                started=(
                    min(created) - BIND_LOOKBACK,
                    max(created) + BIND_CLOCK_SLACK,
                ),
            )
            for link in group:
                key = (link.scope, link.parent)
                span = spans.get(link.parent.span_id)
                if key in result or span is None:
                    continue
                if getattr(span, "is_deleted", 1):
                    result[key] = ParentState(
                        link.scope, link.parent, False, {}, None, None
                    )
                    continue
                try:
                    parent = occurrences.Parent(
                        UUID(link.parent.trace_id), link.parent.span_id
                    )
                    same_trace = UUID(str(span.trace_id)) == parent.trace_id
                except (ValueError, TypeError, AttributeError):
                    continue  # A malformed stored trace id proves nothing.
                if not same_trace or str(span.project_id) != str(project_id):
                    continue  # Another trace's span id is never this parent.
                state = source_state(scope, parent, span)
                result[key] = ParentState(
                    link.scope,
                    link.parent,
                    True,
                    _references(span),
                    state,
                    state.expires_at if state else None,
                )
        return result


def reconcile_tick(*, now=None, span_reader=read_spans):
    """One scheduled pass: at most ``MAX_TICK_BATCHES`` bounded batches."""
    now = now or timezone.now()
    store = OccurrenceBindingStore()
    reader = ClickHouseParentReader(span_reader)
    totals = dict.fromkeys(("bound", "pending", "rejected", "expired", "conflict"), 0)
    for _ in range(MAX_TICK_BATCHES):
        counts = reconcile_pending(store, reader, now=now, limit=MAX_BATCH)
        for state, count in counts.items():
            totals[state] += count
        if sum(counts.values()) < MAX_BATCH:
            break
    return totals


def _audio_facts(row):
    """Validated blob facts of a READY standalone audio upload, else None.

    Only what the sandboxed probe proved (type, rate, channels, frames); the
    Voice projection derives a playable manifest from them. Segments of a
    server recording (``recording_id``) are described by their manifest.
    """
    attachment = row.attachment
    if (
        row.recording_id
        or attachment.modality != "audio"
        or attachment.state != AttachmentState.READY
    ):
        return None
    try:
        blob = attachment.attachmentblob
    except AttachmentBlob.DoesNotExist:
        return None
    if blob is None or blob.mime_type != attachment.mime_type:
        return None
    return {
        "mime_type": blob.mime_type,
        "sample_rate": blob.sample_rate,
        "channels": blob.channels,
        "frame_count": blob.frame_count,
    }


class _MemoAuthority:
    """One live-session check per viewer and one source read per parent."""

    def __init__(self, authority):
        self.authority, self.sessions, self.sources = authority, {}, {}

    def session_is_live(self, viewer):
        if viewer not in self.sessions:
            self.sessions[viewer] = self.authority.session_is_live(viewer)
        return self.sessions[viewer]

    def source(self, viewer, scope, parent):
        key = (viewer, scope.id, parent)
        if key not in self.sources:
            self.sources[key] = self.authority.source(viewer, scope, parent)
        return self.sources[key]


class ControlPlaneReader:
    """``AuthorizedAttachmentReader`` over the control plane for app sessions.

    Only a live application session of a user who can read the project gets
    metadata; API-key callers, other projects and any denial read as nothing.
    """

    def __init__(self, span_reader=read_spans):
        self.span_reader = span_reader

    def _viewer(self, request, scope):
        project = Project.no_workspace_objects.filter(
            pk=scope.project_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            deleted=False,
        ).first()
        if project is None:
            return None
        try:
            return principal.project_viewer(principal.identity(request), project)
        except AttachmentError:
            return None

    def _authority(self, viewer, span_ids, *, trace_ids=(), started=()):
        spans = self.span_reader(
            viewer.project_id, span_ids, trace_ids=trace_ids, started=started
        )
        return self._memo(spans)

    def _memo(self, spans):
        """The live authority over ``spans`` (a dict filled by one batch read)."""
        return _MemoAuthority(
            LiveAuthority(span_reader=lambda project_id, span_id: spans.get(span_id))
        )

    @staticmethod
    def _parents(group):
        """``{(trace UUID, span id): target}``; a malformed trace id is skipped."""
        parents = {}
        for target in group:
            try:
                key = (UUID(str(target.parent.trace_id)), target.parent.span_id)
            except (ValueError, TypeError, AttributeError):
                continue
            parents[key] = target
        return parents

    def list_authorized(self, viewer, targets, *, per_parent_limit):
        """One scope check, one row query and one span read per scope.

        Rows come with their attachment, capture session and recording in the
        same query and are authorized in memory by ``occurrences.check_readable``,
        the gate ``get_occurrence`` applies. Nothing here reads the blob: only a
        ready standalone recording needs its facts (``standalone_recording``).
        """
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False) or not targets:
            return []
        groups = {}
        for target in targets:
            groups.setdefault(target.scope, []).append(target)
        statuses, budget = [], MAX_READ_ROWS
        for scope_key, group in groups.items():
            if budget <= 0:
                break
            app_viewer = self._viewer(viewer, scope_key)
            parents = self._parents(group)
            if app_viewer is None or not parents:
                continue
            spans = {}
            authority = self._memo(spans)
            try:
                scope = occurrences.scope_for(app_viewer, authority)
            except AttachmentError:
                continue
            match = Q()
            for trace_id, span_id in parents:
                match |= Q(trace_id=trace_id, span_id=span_id)
            rows = list(
                AttachmentOccurrence.objects.select_related(
                    "attachment", "capture_session", "recording"
                )
                .filter(
                    match,
                    scope=scope,
                    bound_at__isnull=False,
                    deleted_at__isnull=True,
                )
                .order_by("trace_id", "span_id", "id")[
                    : min(budget, len(parents) * per_parent_limit)
                ]
            )
            if not rows:
                continue
            budget -= len(rows)
            started = [row.source_started_at for row in rows if row.source_started_at]
            spans.update(
                self.span_reader(
                    app_viewer.project_id,
                    {row.span_id for row in rows},
                    trace_ids={row.trace_id for row in rows},
                    started=(min(started), max(started)) if started else (),
                )
            )
            now = timezone.now()
            for row in rows:
                parent = occurrences.Parent(row.trace_id, row.span_id)
                try:
                    state = occurrences.source_for(app_viewer, authority, scope, parent)
                    occurrences.check_readable(row, scope, state, now)
                except AttachmentError:
                    continue
                expiry = min(row.expires_at, row.source_expires_at, state.expires_at)
                recording = row.recording if row.recording_id else None
                statuses.append(
                    AuthorizedStatus(
                        target=parents[(row.trace_id, row.span_id)],
                        reference=occurrences.metadata(row, scope, expiry)["reference"],
                        state=row.attachment.state,
                        bound=True,
                        # A segment is complete when its recording's manifest
                        # says so; a standalone upload is its whole capture.
                        capture_complete=(
                            recording.capture_complete
                            if recording is not None
                            else row.attachment.state == "ready"
                        ),
                        expires_at=expiry,
                        recording_id=(
                            str(recording.client_id) if recording is not None else None
                        ),
                    )
                )
        return statuses

    def recording_manifest(self, viewer, target, recording_id):
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
            return None
        app_viewer = self._viewer(viewer, target.scope)
        if app_viewer is None:
            return None
        try:
            return manifests.read_manifest(
                app_viewer,
                self._authority(app_viewer, {target.parent.span_id}),
                recording_id=recording_id,
                parent={
                    "trace_id": target.parent.trace_id,
                    "span_id": target.parent.span_id,
                },
            )
        except AttachmentError:
            return None

    def standalone_recording(self, viewer, target, occurrence_id):
        """Blob facts of one READY standalone audio occurrence, or None.

        The same gate as every other read (``get_occurrence`` with
        ``ready=True``) for this exact parent, so a denial, a tombstone or a
        stale policy reads as None. Never media, never a URL.
        """
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
            return None
        app_viewer = self._viewer(viewer, target.scope)
        if app_viewer is None:
            return None
        try:
            parent = occurrences.Parent(
                UUID(str(target.parent.trace_id)), target.parent.span_id
            )
            row, _, _ = occurrences.get_occurrence(
                app_viewer,
                self._authority(
                    app_viewer, {parent.span_id}, trace_ids={parent.trace_id}
                ),
                occurrence_id,
                parent,
                ready=True,
            )
        except (AttachmentError, ValueError, TypeError, AttributeError):
            return None
        return _audio_facts(row)

"""Concrete ingest adapters over the attachment control plane.

- ``OccurrenceBindingStore``: the durable pending set is the unbound live
  ``AttachmentOccurrence`` rows themselves (B01). Claims are round-robin across
  scopes, least recently checked first, with a per-scope cap per tick; a row
  that stays pending is stamped (``bind_checked_at``) and goes to the back.
  Binding goes through ``retention.bind_retention`` (fenced CAS, B05/B09);
  expiry, rejection and permanent bind fences (stale policy or deletion
  generation) tombstone through ``retention._tombstone``, which enqueues the
  erase outbox.
- ``ClickHouseParentReader``: one ``spans FINAL`` batch per project per tick.
  ``source_state`` derives the stored source facts with the same rule as
  ``authority.LiveAuthority.source``, so binding and reading cannot disagree.
- ``ControlPlaneReader``: the B10 reader installed on trace and Voice reads.
  It re-authorizes every occurrence with ``occurrences.get_occurrence`` for the
  live application session; with ``FI_ATTACHMENTS_ENABLED`` off it does no I/O.

No adapter fetches media, follows a URL or trusts an OTLP timestamp.
"""

from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Window
from django.db.models.functions import Coalesce, RowNumber
from django.utils import timezone

from tracer.models.attachment import (
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


def read_spans(project_id, span_ids, *, with_attributes=False):
    """``{span_id: span}`` from one ``spans FINAL`` read for one project."""
    from tracer.services.clickhouse.v2 import get_reader

    columns = SPAN_COLUMNS + (["attrs_string"] if with_attributes else [])
    rows = get_reader().list_by_ids(
        sorted(span_ids), project_id=str(project_id), columns=columns
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


class OccurrenceBindingStore:
    """Pending links are unbound, undeleted occurrences of enabled scopes.

    One store lives for one tick. Within a tick no row is claimed twice; across
    ticks the order is the persisted ``bind_checked_at`` stamp (never-checked
    rows by age), so rows that keep waiting cannot starve newer ones.
    """

    def __init__(self):
        self.claimed = set()
        self.per_scope = Counter()

    def claim_pending(self, now, limit):
        pending = AttachmentOccurrence.objects.filter(
            bound_at__isnull=True,
            deleted_at__isnull=True,
            scope__enabled=True,
        ).exclude(
            # Aborted or erased bytes have nothing left to bind. Failed and
            # rejected uploads still bind, so readers can show that state.
            attachment__state__in=(AttachmentState.DELETED, AttachmentState.EXPIRED)
        )
        if self.claimed:
            pending = pending.exclude(id__in=self.claimed)
        full = [
            scope_id
            for scope_id, count in self.per_scope.items()
            if count >= PER_SCOPE_TICK_CAP
        ]
        if full:
            pending = pending.exclude(scope_id__in=full)
        # Round-robin: every scope's least recently checked row first, then
        # every scope's second, and so on.
        ids = list(
            pending.annotate(touched=Coalesce("bind_checked_at", "created_at"))
            .annotate(
                turn=Window(
                    RowNumber(),
                    partition_by=[F("scope_id")],
                    order_by=[F("touched").asc(), F("id").asc()],
                )
            )
            .filter(turn__lte=PER_SCOPE_TICK_CAP)
            .order_by("turn", "touched", "id")
            .values_list("id", "scope_id")[:limit]
        )
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
        groups = {}
        for link in links:
            groups.setdefault((link.scope_id, link.scope.project_id), []).append(link)
        scopes = AttachmentScope.objects.in_bulk([scope_id for scope_id, _ in groups])
        result = {}
        for (scope_id, project_id), group in groups.items():
            scope = scopes.get(scope_id)
            if scope is None:
                continue
            spans = self.span_reader(
                project_id,
                {link.parent.span_id for link in group},
                with_attributes=True,
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
                parent = occurrences.Parent(
                    UUID(link.parent.trace_id), link.parent.span_id
                )
                try:
                    same_trace = UUID(str(span.trace_id)) == parent.trace_id
                except (ValueError, TypeError):
                    same_trace = False
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

    def _authority(self, viewer, span_ids):
        spans = self.span_reader(viewer.project_id, span_ids)
        return _MemoAuthority(
            LiveAuthority(span_reader=lambda project_id, span_id: spans.get(span_id))
        )

    def list_authorized(self, viewer, targets, *, per_parent_limit):
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False) or not targets:
            return []
        groups = {}
        for target in targets:
            groups.setdefault(target.scope, []).append(target)
        statuses, budget = [], MAX_READ_ROWS
        for scope, group in groups.items():
            app_viewer = self._viewer(viewer, scope)
            if app_viewer is None or budget <= 0:
                continue
            parents = {
                (UUID(target.parent.trace_id), target.parent.span_id): target
                for target in group
            }
            match = Q()
            for trace_id, span_id in parents:
                match |= Q(trace_id=trace_id, span_id=span_id)
            rows = list(
                AttachmentOccurrence.objects.filter(
                    match,
                    scope__project_id=app_viewer.project_id,
                    bound_at__isnull=False,
                    deleted_at__isnull=True,
                )
                .order_by("trace_id", "span_id", "id")
                .values_list("client_id", "trace_id", "span_id")[
                    : min(budget, len(parents) * per_parent_limit)
                ]
            )
            if not rows:
                continue
            budget -= len(rows)
            authority = self._authority(app_viewer, {span for _, _, span in rows})
            for client_id, trace_id, span_id in rows:
                try:
                    row, current, expiry = occurrences.get_occurrence(
                        app_viewer,
                        authority,
                        client_id,
                        occurrences.Parent(trace_id, span_id),
                    )
                except AttachmentError:
                    continue
                recording = row.recording if row.recording_id else None
                statuses.append(
                    AuthorizedStatus(
                        target=parents[(trace_id, span_id)],
                        reference=occurrences.metadata(row, current, expiry)[
                            "reference"
                        ],
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

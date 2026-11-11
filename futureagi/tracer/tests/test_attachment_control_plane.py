"""PostgreSQL components for the concrete ingest adapters.

The ClickHouse span reader is an explicit double with the exact column set the
production reader selects; the real ``spans FINAL`` read is not exercised here.
Negative controls mutate one fact of a fixture that otherwise binds.
"""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tfc.temporal.schedules.attachments import ATTACHMENT_SCHEDULES
from tracer.models.attachment import (
    Attachment,
    AttachmentAuditEvent,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
)
from tracer.services.attachments import (
    broker,
    control_plane,
    manifests,
    occurrences,
    uploads,
    validation,
)
from tracer.services.attachments.authority import LiveAuthority
from tracer.services.attachments.binding import Parent, Scope
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.probe import probe
from tracer.services.attachments.reads import ReadTarget
from tracer.services.attachments.references import REFS
from tracer.tasks.attachment_binding import reconcile_attachment_bindings
from tracer.tests import test_attachment_core as core
from tracer.tests.test_attachment_core import ImmutableStore, initialized, wav_bytes

setup = core.setup

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture(autouse=True)
def unlimited_retention(monkeypatch):
    """Self-hosted retention (no cutoff); finite and unknown cases override it."""
    import tfc.ee_gates

    monkeypatch.setattr(
        tfc.ee_gates, "trace_retention_cutoff", lambda organization_id, now=None: None
    )


class Spans:
    """``read_spans`` double: one dict per project, keyed by span id."""

    def __init__(self):
        self.by_project, self.calls = {}, []

    def put(self, project_id, span):
        self.by_project.setdefault(str(project_id), {})[span.id] = span

    def __call__(self, project_id, span_ids, *, with_attributes=False):
        self.calls.append((str(project_id), frozenset(span_ids), with_attributes))
        rows = self.by_project.get(str(project_id), {})
        return {span_id: rows[span_id] for span_id in span_ids if span_id in rows}


def ref_for(row, **changes):
    ref = {
        "attachment_id": str(row.attachment.client_id),
        "occurrence_id": str(row.client_id),
        "direction": row.direction,
        "modality": row.attachment.modality,
        "mime_type": row.attachment.mime_type,
    }
    ref.update(changes)
    return ref


def span_for(
    row, *, refs=None, deleted=0, trace_id=None, project_id=None, start_time=None
):
    return SimpleNamespace(
        id=row.span_id,
        trace_id=str(trace_id or row.trace_id),
        project_id=str(project_id or row.scope.project_id),
        start_time=start_time or timezone.now() - timedelta(minutes=5),
        is_deleted=deleted,
        attrs_string={REFS: json.dumps([ref_for(row)] if refs is None else refs)},
    )


def ready_upload(setup, monkeypatch, **overrides):
    scope, _ = setup
    upload, token, _, _ = initialized(setup, **overrides)
    store = ImmutableStore()
    uploads.transfer(
        token,
        scope_id=scope.id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=store,
    )
    uploads.complete(token, scope_id=scope.id, upload_id=upload.id)
    # Explicit parser double, NOT sandbox qualification.
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    validation.validate(upload.id, scope_id=scope.id, storage=store)
    upload.refresh_from_db()
    return upload, token


def occurrence(setup, monkeypatch, **overrides):
    upload, _ = ready_upload(setup, monkeypatch, **overrides)
    return AttachmentOccurrence.objects.select_related("attachment", "scope").get(
        pk=upload.occurrence_id
    )


class LiveSession(LiveAuthority):
    """The real source rule; only the application-session check is a double."""

    def session_is_live(self, viewer):
        return True


def viewer_for(scope, user):
    return occurrences.Viewer(
        user.id,
        uuid.uuid4(),
        scope.organization_id,
        scope.workspace_id,
        scope.project_id,
    )


def test_bound_source_equals_the_read_path_source(setup, user, monkeypatch):
    """Binding stores exactly what every later read derives, so reads agree."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    row.refresh_from_db()
    parent = occurrences.Parent(row.trace_id, row.span_id)
    authority = LiveSession(
        span_reader=lambda project_id, span_id: spans(project_id, {span_id}).get(
            span_id
        )
    )
    viewer = viewer_for(scope, user)
    read = authority.source(viewer, scope, parent)
    assert (row.source_started_at, row.source_expires_at, row.source_generation) == (
        read.started_at,
        read.expires_at,
        read.generation,
    )
    found, _, _ = occurrences.get_occurrence(
        viewer, authority, row.client_id, parent, ready=True
    )
    assert found.pk == row.pk
    # The worker reads spans with attributes, one batch per project.
    assert spans.calls[0] == (str(scope.project_id), frozenset({row.span_id}), True)
    # Idempotent: nothing is pending on the next tick.
    assert control_plane.reconcile_tick(span_reader=spans) == dict.fromkeys(
        ("bound", "pending", "rejected", "expired", "conflict"), 0
    )


def test_binding_waits_without_any_sdk_retry(setup, monkeypatch):
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    # No span yet: the row waits, and is stamped to the back of the queue.
    assert control_plane.reconcile_tick(span_reader=spans)["pending"] == 1
    row.refresh_from_db()
    assert row.bound_at is None and row.deleted_at is None
    assert row.bind_checked_at is not None
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1


def _binds_without_a_reference(scope, row, refs):
    """The authenticated parent + live span + known retention is the proof."""
    spans = Spans()
    span = span_for(row, refs=refs)
    assert str(row.client_id) not in control_plane._references(span)
    spans.put(scope.project_id, span)
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert counts == dict(
        dict.fromkeys(("bound", "pending", "rejected", "expired", "conflict"), 0),
        bound=1,
    )
    row.refresh_from_db()
    assert row.bound_at is not None and row.deleted_at is None
    assert row.source_generation == 0
    # Still bound after the 24 h pending window: nothing erases it.
    AttachmentOccurrence.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    control_plane.reconcile_tick(span_reader=spans)
    row.refresh_from_db()
    assert row.deleted_at is None
    assert not AttachmentOutbox.objects.filter(
        scope=scope, upload__occurrence=row, event="erase"
    ).exists()


def test_recording_segment_binds_with_no_span_reference(setup, monkeypatch):
    scope, _ = setup
    scope.record_audio, scope.policy_version = True, scope.policy_version + 1
    scope.save(update_fields=["record_audio", "policy_version"])
    upload, _ = ready_upload(
        setup,
        monkeypatch,
        recording_id=str(uuid.uuid4()),
        segment_index=0,
        purpose="conversation_recording",
    )
    row = AttachmentOccurrence.objects.select_related("attachment", "scope").get(
        pk=upload.occurrence_id
    )
    _binds_without_a_reference(scope, row, refs=[])


def test_late_attach_binds_with_no_span_reference(setup, monkeypatch):
    """The span was exported (ended) before the upload; it never names it."""
    scope, _ = setup
    _binds_without_a_reference(scope, occurrence(setup, monkeypatch), refs=[])


def test_occurrence_past_the_reference_cap_binds(setup, monkeypatch):
    """The 65th occurrence: the span carries 64 other references, not this one."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    others = [
        ref_for(row, attachment_id=str(uuid.uuid4()), occurrence_id=str(uuid.uuid4()))
        for _ in range(64)
    ]
    assert len(control_plane._references(span_for(row, refs=others))) == 64
    _binds_without_a_reference(scope, row, refs=others)


@pytest.mark.parametrize(
    "mutation,outcome",
    [
        ("foreign_project", "pending"),  # another project's span is never read
        ("other_trace", "pending"),  # same span id in another trace
        ("unknown_retention", "pending"),
        ("deleted_span", "expired"),
        ("other_attachment", "rejected"),
        ("other_direction", "rejected"),
        ("other_modality", "rejected"),
        ("tail_dropped", "expired"),
    ],
)
def test_negative_controls_never_bind(setup, monkeypatch, mutation, outcome):
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    span = span_for(row)
    if mutation == "foreign_project":
        spans.put(uuid.uuid4(), span_for(row, project_id=uuid.uuid4()))
        span = None
    elif mutation == "other_trace":
        span = span_for(row, trace_id=uuid.uuid4())
    elif mutation == "unknown_retention":
        import tfc.ee_gates

        def unavailable(organization_id, now=None):
            raise tfc.ee_gates.RetentionPolicyUnavailable()

        monkeypatch.setattr(tfc.ee_gates, "trace_retention_cutoff", unavailable)
    elif mutation == "deleted_span":
        span = span_for(row, deleted=1)
    elif mutation == "other_attachment":
        span = span_for(row, refs=[ref_for(row, attachment_id=str(uuid.uuid4()))])
    elif mutation == "other_direction":
        span = span_for(row, refs=[ref_for(row, direction="input")])
    elif mutation == "other_modality":
        span = span_for(
            row, refs=[ref_for(row, modality="image", mime_type="image/png")]
        )
    elif mutation == "tail_dropped":
        span = None
        AttachmentOccurrence.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
    if span is not None:
        spans.put(scope.project_id, span)
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert counts[outcome] == 1 and counts["bound"] == 0
    row.refresh_from_db()
    assert row.bound_at is None
    if outcome == "pending":
        assert row.deleted_at is None
        return
    # Expired and rejected rows are tombstoned and their bytes queued for erase.
    assert row.deleted_at is not None
    assert AttachmentAuditEvent.objects.filter(
        scope=scope, entity_id=row.id, event="deletion", reason="retention"
    ).exists()
    assert AttachmentOutbox.objects.filter(
        scope=scope, upload__occurrence=row, event="erase"
    ).exists()


def test_fences_and_disabled_scopes_are_not_bound(setup, monkeypatch):
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(row))
    # The scope trigger requires a policy version bump with every switch.
    scope.enabled, scope.policy_version = False, scope.policy_version + 1
    scope.save(update_fields=["enabled", "policy_version"])
    assert control_plane.reconcile_tick(span_reader=spans) == dict.fromkeys(
        ("bound", "pending", "rejected", "expired", "conflict"), 0
    )
    # Re-enabled under a newer policy: the upload's policy no longer matches.
    # No later tick can pass that fence, so the row is retired, not retried.
    scope.enabled, scope.policy_version = True, scope.policy_version + 1
    scope.save(update_fields=["enabled", "policy_version"])
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert counts["rejected"] == 1 and counts["bound"] == 0
    row.refresh_from_db()
    assert row.bound_at is None and row.deleted_at is not None
    assert AttachmentOutbox.objects.filter(
        scope=scope, upload__occurrence=row, event="erase"
    ).exists()
    assert control_plane.reconcile_tick(span_reader=spans) == dict.fromkeys(
        ("bound", "pending", "rejected", "expired", "conflict"), 0
    )


def test_transient_bind_fence_parks_and_never_erases(setup, monkeypatch):
    """A source that starts in the future fails the CAS but is not permanent."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(
        scope.project_id,
        span_for(row, start_time=timezone.now() + timedelta(hours=1)),
    )
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert counts["conflict"] == 1 and counts["bound"] == 0
    row.refresh_from_db()
    assert row.bound_at is None and row.deleted_at is None
    assert row.bind_checked_at is not None
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1


def test_claims_walk_past_a_full_batch_of_waiting_rows(setup, monkeypatch):
    scope, _ = setup
    first = occurrence(setup, monkeypatch)
    second = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(second))
    store = control_plane.OccurrenceBindingStore()
    assert [link.internal_id for link in store.claim_pending(timezone.now(), 1)] == [
        first.id
    ]
    assert [link.internal_id for link in store.claim_pending(timezone.now(), 1)] == [
        second.id
    ]
    monkeypatch.setattr(control_plane, "MAX_BATCH", 1)
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert (counts["pending"], counts["bound"]) == (1, 1)


class Reader(control_plane.ControlPlaneReader):
    """The real reader with the JWT step replaced by an explicit viewer."""

    def __init__(self, viewer, spans):
        super().__init__(span_reader=spans)
        self.viewer = viewer

    def _viewer(self, request, scope):
        if str(scope.project_id) != str(self.viewer.project_id):
            return None
        return self.viewer

    def _authority(self, viewer, span_ids):
        spans = self.span_reader(viewer.project_id, span_ids)
        return control_plane._MemoAuthority(
            LiveSession(span_reader=lambda project_id, span_id: spans.get(span_id))
        )


def target_for(scope, row):
    return ReadTarget(
        Scope(
            str(scope.organization_id), str(scope.workspace_id), str(scope.project_id)
        ),
        Parent(str(row.trace_id), row.span_id),
    )


def test_reader_is_dark_when_disabled(setup, settings, django_assert_num_queries):
    scope, _ = setup
    settings.FI_ATTACHMENTS_ENABLED = False
    reader = control_plane.ControlPlaneReader(span_reader=Spans())
    target = ReadTarget(
        Scope(
            str(scope.organization_id), str(scope.workspace_id), str(scope.project_id)
        ),
        Parent(str(uuid.uuid4()), core.SPAN),
    )
    with django_assert_num_queries(0):
        assert reader.list_authorized(object(), (target,), per_parent_limit=64) == []
        assert reader.recording_manifest(object(), target, str(uuid.uuid4())) is None
    assert reconcile_attachment_bindings._original_func() == {"disabled": True}
    assert ATTACHMENT_SCHEDULES == []


def test_reader_returns_only_bound_authorized_occurrences(setup, user, monkeypatch):
    scope, _ = setup
    bound = occurrence(setup, monkeypatch)
    unbound = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(bound, refs=[ref_for(bound)]))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    viewer = viewer_for(scope, user)
    reader = Reader(viewer, spans)
    target = target_for(scope, bound)
    (status,) = reader.list_authorized(object(), (target,), per_parent_limit=64)
    assert status.target == target
    assert status.reference["occurrence_id"] == str(bound.client_id)
    assert (status.state, status.bound, status.capture_complete) == (
        "ready",
        True,
        True,
    )
    assert status.recording_id is None
    assert str(unbound.client_id) != status.reference["occurrence_id"]
    # A foreign project reads as nothing; a deleted source reads as nothing.
    foreign = ReadTarget(
        Scope(str(scope.organization_id), str(scope.workspace_id), str(uuid.uuid4())),
        target.parent,
    )
    assert reader.list_authorized(object(), (foreign,), per_parent_limit=64) == []
    spans.put(scope.project_id, span_for(bound, deleted=1))
    assert reader.list_authorized(object(), (target,), per_parent_limit=64) == []


def test_reader_serves_the_authorized_recording_manifest(setup, user, monkeypatch):
    scope, _ = setup
    scope.record_audio, scope.policy_version = True, scope.policy_version + 1
    scope.save(update_fields=["record_audio", "policy_version"])
    recording_id = str(uuid.uuid4())
    upload, token = ready_upload(
        setup,
        monkeypatch,
        recording_id=recording_id,
        segment_index=0,
        purpose="conversation_recording",
    )
    row = AttachmentOccurrence.objects.select_related("attachment", "scope").get(
        pk=upload.occurrence_id
    )
    track = str(uuid.uuid4())
    body = {
        "schema_version": 1,
        "recording_id": recording_id,
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": 16000,
        "channels": 1,
        "codec": "pcm",
        "container": "wav",
        "layout": "mixed",
        "tracks": [
            {"track_id": track, "channels": [0], "direction": "mixed", "role": "mixed"}
        ],
        "segments": [
            {
                "occurrence_id": str(row.client_id),
                "index": 0,
                "start_ms": 0,
                "duration_ms": 1000,
            }
        ],
        "gaps": [],
        "playout": [],
        "capture_complete": True,
        "duration_ms": 1000,
    }
    manifests.put_manifest(
        token, scope_id=scope.id, recording_id=recording_id, body=body, key="m-1"
    )
    spans = Spans()
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    reader = Reader(viewer_for(scope, user), spans)
    target = target_for(scope, row)
    (status,) = reader.list_authorized(object(), (target,), per_parent_limit=64)
    assert status.recording_id == recording_id and status.capture_complete is True
    assert status.reference["purpose"] == "conversation_recording"
    assert reader.recording_manifest(object(), target, recording_id) == body
    # Another parent never reveals the manifest.
    other = ReadTarget(target.scope, Parent(str(uuid.uuid4()), row.span_id))
    assert reader.recording_manifest(object(), other, recording_id) is None


def test_read_path_refuses_what_binding_refused(setup, user, monkeypatch):
    """No read path reaches an unbound occurrence, even with a live source."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    # A reference that disagrees with the upload rejects the occurrence.
    spans.put(scope.project_id, span_for(row, refs=[ref_for(row, direction="input")]))
    assert control_plane.reconcile_tick(span_reader=spans)["rejected"] == 1
    authority = LiveSession(
        span_reader=lambda project_id, span_id: spans(project_id, {span_id}).get(
            span_id
        )
    )
    with pytest.raises(AttachmentError):
        occurrences.get_occurrence(
            viewer_for(scope, user),
            authority,
            row.client_id,
            occurrences.Parent(row.trace_id, row.span_id),
        )


def test_aborted_or_erased_bytes_are_never_claimed(setup, monkeypatch):
    scope, _ = setup
    aborted, token, _, _ = initialized(setup)
    uploads.abort(token, scope_id=scope.id, upload_id=aborted.id)
    expired, _, _, _ = initialized(setup)
    uploads.settle(expired.id, scope_id=scope.id, outcome=AttachmentState.EXPIRED)
    assert set(
        Attachment.objects.filter(
            pk__in=[aborted.attachment_id, expired.attachment_id]
        ).values_list("state", flat=True)
    ) == {"deleted", "expired"}
    # Positive control on the same parent: live bytes still bind.
    live = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(live))
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert counts == dict(
        dict.fromkeys(("bound", "pending", "rejected", "expired", "conflict"), 0),
        bound=1,
    )
    assert (
        AttachmentOccurrence.objects.filter(
            pk__in=[aborted.occurrence_id, expired.occurrence_id],
            bound_at__isnull=False,
        ).count()
        == 0
    )


@pytest.fixture
def other_setup(observe_project, user, setup):
    """A second enabled scope (another project) in the same workspace."""
    scope = AttachmentScope.objects.create(
        project=observe_project,
        workspace=observe_project.workspace,
        organization=observe_project.organization,
        enabled=True,
        require_media_redaction=False,
    )
    writer = broker.Writer("user", user.pk, scope.organization_id, scope.workspace_id)
    return scope, writer


def test_waiting_rows_never_starve_another_scope(setup, other_setup, monkeypatch):
    """Round-robin claims: an older backlog in one project cannot fill a batch."""
    scope, _ = setup
    waiting = [occurrence(setup, monkeypatch) for _ in range(3)]
    other, _ = other_setup
    bindable = occurrence(other_setup, monkeypatch)
    spans = Spans()  # the waiting rows' spans never arrive
    spans.put(other.project_id, span_for(bindable))
    monkeypatch.setattr(control_plane, "MAX_BATCH", 2)
    monkeypatch.setattr(control_plane, "MAX_TICK_BATCHES", 1)
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert (counts["bound"], counts["pending"]) == (1, 1)
    bindable.refresh_from_db()
    assert bindable.bound_at is not None
    assert all(
        AttachmentOccurrence.objects.get(pk=row.pk).bound_at is None for row in waiting
    )


def test_waiting_rows_rotate_within_a_scope(setup, monkeypatch):
    """A parked row goes to the back, so a newer bindable row is reached."""
    scope, _ = setup
    waiting = [occurrence(setup, monkeypatch) for _ in range(2)]
    bindable = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(bindable))
    monkeypatch.setattr(control_plane, "MAX_BATCH", 1)
    monkeypatch.setattr(control_plane, "MAX_TICK_BATCHES", 1)
    seen = []
    for _ in range(3):
        store = control_plane.OccurrenceBindingStore()
        (link,) = store.claim_pending(timezone.now(), 1)
        seen.append(link.internal_id)
        control_plane.reconcile_tick(span_reader=spans)
    assert seen == [waiting[0].id, waiting[1].id, bindable.id]
    bindable.refresh_from_db()
    assert bindable.bound_at is not None


def test_one_scope_is_capped_per_tick(setup, other_setup, monkeypatch):
    scope, _ = setup
    for _ in range(3):
        occurrence(setup, monkeypatch)
    other, _ = other_setup
    occurrence(other_setup, monkeypatch)
    monkeypatch.setattr(control_plane, "PER_SCOPE_TICK_CAP", 1)
    store = control_plane.OccurrenceBindingStore()
    first = store.claim_pending(timezone.now(), 4)
    assert sorted(str(link.scope_id) for link in first) == sorted(
        [str(scope.id), str(other.id)]
    )
    # Both scopes reached their cap for this tick; nothing else is claimed.
    assert store.claim_pending(timezone.now(), 4) == []
    counts = control_plane.reconcile_tick(span_reader=Spans())
    assert counts["pending"] == 2

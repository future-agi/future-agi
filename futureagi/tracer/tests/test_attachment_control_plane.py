"""PostgreSQL components for the concrete ingest adapters.

The ClickHouse span reader is an explicit double with the exact column set the
production reader selects; the real ``spans FINAL`` read is not exercised here.
Negative controls mutate one fact of a fixture that otherwise binds.
"""

import json
import uuid
from datetime import UTC, timedelta
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
    control_plane,
    lifecycle,
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
from tracer.tests.test_attachment_lifecycle import ErasableStore

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
        self.by_project, self.calls, self.batches = {}, [], []

    def put(self, project_id, span):
        self.by_project.setdefault(str(project_id), {})[span.id] = span

    def __call__(
        self, project_id, span_ids, *, with_attributes=False, trace_ids=(), started=()
    ):
        self.calls.append((str(project_id), frozenset(span_ids), with_attributes))
        self.batches.append(
            (frozenset(str(trace_id) for trace_id in trace_ids), tuple(started))
        )
        rows = self.by_project.get(str(project_id), {})
        return {
            span_id: rows[span_id]
            for span_id in span_ids
            if span_id in rows
            # Like the real read: the batch is keyed by trace and time window.
            and (not trace_ids or str(rows[span_id].trace_id) in self.batches[-1][0])
            and (not started or _hour(started[0]) <= _hour(rows[span_id].start_time))
            and (not started or _hour(rows[span_id].start_time) <= _hour(started[1]))
        }


def _hour(value):
    """``toStartOfHour``: the real read's window is in whole hour buckets."""
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


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


def occurrence_on(setup, monkeypatch, span_id):
    """An occurrence whose authenticated parent is another span of TRACE."""
    current, _ = core.session(setup)
    association = {
        "capture_session_id": str(current.client_id),
        "correlation_nonce": str(current.correlation_nonce),
        "parent": {"trace_id": core.TRACE, "span_id": span_id},
    }
    return occurrence(setup, monkeypatch, current=current, association=association)


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


def test_bound_occurrence_still_reads_25h_later_through_the_scheduled_jobs(
    setup, user, monkeypatch
):
    """Regression: binding kept the 24 h pending deadline, so the sweep erased
    every bound occurrence one day after upload. The real binder, real source
    rule and real read path; only the clock and the span store are doubles."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    row.refresh_from_db()
    assert row.expires_at == row.source_expires_at
    parent = occurrences.Parent(row.trace_id, row.span_id)
    authority = LiveSession(
        span_reader=lambda project_id, span_id: spans(project_id, {span_id}).get(
            span_id
        )
    )
    viewer = viewer_for(scope, user)
    for hours in (25, 24 * 30):
        _travel(monkeypatch, timedelta(hours=hours))
        swept, drained = _scheduled_tick(spans)
        assert swept.get("occurrences_expired", 0) == 0, (hours, swept)
        assert "erase_done" not in drained, (hours, drained)
        found, _, expiry = occurrences.get_occurrence(
            viewer, authority, row.client_id, parent, ready=True
        )
        assert found.pk == row.pk and found.deleted_at is None
        assert expiry == row.source_expires_at
    assert not AttachmentOutbox.objects.filter(scope=scope, event="erase").exists()


def test_unbound_occurrence_still_expires_after_the_24h_deadline(setup, monkeypatch):
    """Control: with no span the binder never binds, and the deadline holds."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    assert control_plane.reconcile_tick(span_reader=spans)["pending"] == 1
    _travel(monkeypatch, timedelta(hours=25))
    swept, drained = _scheduled_tick(spans)
    row.refresh_from_db()
    assert row.bound_at is None and row.deleted_at is not None
    assert drained.get("erase_done") == 1, drained


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


def _travel(monkeypatch, delta):
    """Move every service's clock (they all read ``timezone.now``)."""
    later = timezone.now() + delta
    monkeypatch.setattr(timezone, "now", lambda: later)
    return later


def _scheduled_tick(spans):
    """The scheduled binder, lifecycle sweep and outbox drain, all rows due."""
    control_plane.reconcile_tick(span_reader=spans)
    swept = lifecycle.sweep()
    AttachmentOutbox.objects.filter(processed_at__isnull=True).update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    return swept, lifecycle.drain_outbox(storage=ErasableStore())


def _binds_without_a_reference(scope, row, refs, monkeypatch):
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
    # Bound: it takes the source retention, not the 24 h pending deadline.
    assert row.expires_at == row.source_expires_at
    assert row.expires_at - row.created_at > timedelta(days=365)
    # Still bound 25 h later, through the real scheduled sweep and erase drain
    # (moving only created_at, as this test once did, proved nothing).
    _travel(monkeypatch, timedelta(hours=25))
    swept, drained = _scheduled_tick(spans)
    row.refresh_from_db()
    assert swept.get("occurrences_expired", 0) == 0, swept
    assert "erase_done" not in drained, drained
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
    _binds_without_a_reference(scope, row, refs=[], monkeypatch=monkeypatch)


def test_late_attach_binds_with_no_span_reference(setup, monkeypatch):
    """The span was exported (ended) before the upload; it never names it."""
    scope, _ = setup
    _binds_without_a_reference(
        scope, occurrence(setup, monkeypatch), refs=[], monkeypatch=monkeypatch
    )


def test_occurrence_past_the_reference_cap_binds(setup, monkeypatch):
    """The 65th occurrence: the span carries 64 other references, not this one."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    others = [
        ref_for(row, attachment_id=str(uuid.uuid4()), occurrence_id=str(uuid.uuid4()))
        for _ in range(64)
    ]
    assert len(control_plane._references(span_for(row, refs=others))) == 64
    _binds_without_a_reference(scope, row, refs=others, monkeypatch=monkeypatch)


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
    first = occurrence_on(setup, monkeypatch, "1111111111111111")
    second = occurrence(setup, monkeypatch)
    spans = Spans()  # first's span never arrives
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

    def _memo(self, spans):
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
    unbound = occurrence_on(setup, monkeypatch, "1111111111111111")
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


def test_binder_tombstone_locks_every_budget_shard_first(setup, monkeypatch):
    """A rejected binding settles an upload of any shard under the scope lock.

    So it takes the retention lock order: every global shard ascending, then
    the organization and project rows, before the scope (#3945's sharding).
    Calling the one-shard ``_lock_budgets(scope)`` here raised TypeError and
    left the row unsettled.
    """
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    spans.put(scope.project_id, span_for(row, refs=[ref_for(row, direction="input")]))
    locked, lock_budget = [], uploads._lock_budget

    def recording(key):
        locked.append(key)
        return lock_budget(key)

    monkeypatch.setattr(uploads, "_lock_budget", recording)
    assert control_plane.reconcile_tick(span_reader=spans)["rejected"] == 1
    expected = [f"global:{shard}" for shard in range(uploads.GLOBAL_SHARDS)] + [
        f"org:{scope.organization_id}",
        f"project:{scope.project_id}",
    ]
    assert locked[: len(expected)] == expected
    row.refresh_from_db()
    assert row.deleted_at is not None


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
    writer = core.broker.Writer(
        "user", user.pk, scope.organization_id, scope.workspace_id
    )
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
    # The waiting rows' spans never arrive.
    waiting = [
        occurrence_on(setup, monkeypatch, span_id)
        for span_id in ("1111111111111111", "2222222222222222")
    ]
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


# --- I04 batch read, placement, standalone recordings, windowed spans --------

PLACEMENT = {
    "message_index": 3,
    "part_index": 1,
    "conversation_id": "conv-1",
    "turn_id": "turn_2",
    "tool_call_id": "call-9",
}


def occurrence_at(setup, monkeypatch, span_id, **overrides):
    """An occurrence on another span of TRACE, with upload overrides."""
    current, _ = core.session(setup)
    association = {
        "capture_session_id": str(current.client_id),
        "correlation_nonce": str(current.correlation_nonce),
        "parent": {"trace_id": core.TRACE, "span_id": span_id},
    }
    return occurrence(
        setup, monkeypatch, current=current, association=association, **overrides
    )


def bound_rows(setup, monkeypatch, count):
    scope, _ = setup
    spans_ids = [f"{index + 1:016x}" for index in range(count)]
    rows = [
        occurrence_at(setup, monkeypatch, span_id, message_index=index, part_index=0)
        for index, span_id in enumerate(spans_ids)
    ]
    spans = Spans()
    for row in rows:
        spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == count
    return rows, spans


def test_reader_emits_the_occurrence_placement(setup, user, monkeypatch):
    scope, _ = setup
    row = occurrence(setup, monkeypatch, **PLACEMENT)
    spans = Spans()
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    reader = Reader(viewer_for(scope, user), spans)
    (status,) = reader.list_authorized(
        object(), (target_for(scope, row),), per_parent_limit=64
    )
    assert {key: status.reference[key] for key in PLACEMENT} == PLACEMENT
    # Absent placement stays absent, never null.
    assert "role" not in status.reference and "channel" not in status.reference


def test_reader_authorizes_a_scope_in_one_batch(setup, user, monkeypatch):
    """Queries per trace read do not grow with the occurrences it returns."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    scope, _ = setup
    rows, spans = bound_rows(setup, monkeypatch, 4)
    reader = Reader(viewer_for(scope, user), spans)
    counts = []
    for subset in (rows[:1], rows):
        spans.calls.clear()
        spans.batches.clear()
        targets = tuple(target_for(scope, row) for row in subset)
        with CaptureQueriesContext(connection) as queries:
            statuses = reader.list_authorized(object(), targets, per_parent_limit=64)
        assert len(statuses) == len(subset)
        counts.append(len(queries))
        # One spans read for the scope, keyed by its traces and start window.
        assert len(spans.calls) == 1
        (traces, window) = spans.batches[0]
        assert traces == {str(row.trace_id) for row in subset}
        started = [
            AttachmentOccurrence.objects.get(pk=row.pk).source_started_at
            for row in subset
        ]
        assert window == (min(started), max(started))
    assert counts[0] == counts[1], counts
    assert [status.reference["message_index"] for status in statuses] == [
        0,
        1,
        2,
        3,
    ]


def test_reader_denies_a_row_in_memory_like_get_occurrence(setup, user, monkeypatch):
    """Deny wins per row: a revoked capture session drops only its rows."""
    scope, _ = setup
    rows, spans = bound_rows(setup, monkeypatch, 2)
    revoked = AttachmentOccurrence.objects.select_related("capture_session").get(
        pk=rows[0].pk
    )
    type(revoked.capture_session).objects.filter(pk=revoked.capture_session_id).update(
        revoked_at=timezone.now()
    )
    reader = Reader(viewer_for(scope, user), spans)
    statuses = reader.list_authorized(
        object(), tuple(target_for(scope, row) for row in rows), per_parent_limit=64
    )
    assert [status.reference["occurrence_id"] for status in statuses] == [
        str(rows[1].client_id)
    ]


def test_reader_skips_a_malformed_trace_id(setup, user, monkeypatch):
    scope, _ = setup
    (row,), spans = bound_rows(setup, monkeypatch, 1)
    good = target_for(scope, row)
    bad = ReadTarget(good.scope, SimpleNamespace(trace_id="not-a-uuid", span_id="1"))
    reader = Reader(viewer_for(scope, user), spans)
    (status,) = reader.list_authorized(object(), (bad, good), per_parent_limit=64)
    assert status.target == good
    assert reader.list_authorized(object(), (bad,), per_parent_limit=64) == []


def test_standalone_recording_facts_follow_the_full_read_gate(setup, user, monkeypatch):
    from tracer.services.attachments.view_enrichment import enrich_voice_detail

    scope, _ = setup
    scope.record_audio, scope.policy_version = True, scope.policy_version + 1
    scope.save(update_fields=["record_audio", "policy_version"])
    row = occurrence(setup, monkeypatch, purpose="conversation_recording")
    spans = Spans()
    spans.put(scope.project_id, span_for(row))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
    reader = Reader(viewer_for(scope, user), spans)
    target = target_for(scope, row)
    facts = reader.standalone_recording(object(), target, str(row.client_id))
    # wav_bytes(): 2 channels at 24 kHz, 240 frames, as the probe validated.
    assert facts == {
        "mime_type": "audio/wav",
        "sample_rate": 24000,
        "channels": 2,
        "frame_count": 240,
    }
    # The real Voice projection plays it through a synthesized manifest.
    request = SimpleNamespace(
        organization=SimpleNamespace(id=scope.organization_id),
        workspace=SimpleNamespace(id=scope.workspace_id),
    )

    def voice_detail():
        result = {
            "project_id": str(scope.project_id),
            "observation_span": [{"id": row.span_id, "trace_id": str(row.trace_id)}],
        }
        enrich_voice_detail(request=request, result=result, reader=reader)
        return result

    result = voice_detail()
    projection = result["recording_attachment"]
    assert projection["state"] == "ready"
    assert projection["manifest"]["segments"][0]["occurrence_id"] == str(row.client_id)
    # Another parent, or a revoked capture session, reads as nothing.
    other = ReadTarget(target.scope, Parent(str(uuid.uuid4()), row.span_id))
    assert reader.standalone_recording(object(), other, str(row.client_id)) is None
    type(row.capture_session).objects.filter(pk=row.capture_session_id).update(
        revoked_at=timezone.now()
    )
    assert reader.standalone_recording(object(), target, str(row.client_id)) is None
    assert "recording_attachment" not in voice_detail()  # the batch denies too


def test_binder_reads_spans_by_trace_within_a_start_window(setup, monkeypatch):
    scope, _ = setup
    rows = [
        occurrence_at(setup, monkeypatch, span_id)
        for span_id in ("1111111111111111", "2222222222222222")
    ]
    spans = Spans()
    for row in rows:
        spans.put(scope.project_id, span_for(row))
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert counts["bound"] == 2
    (traces, (low, high)), *_ = spans.batches
    assert traces == {str(row.trace_id) for row in rows}
    created = [row.created_at for row in rows]
    assert low == min(created) - control_plane.bind_lookback()
    assert high == max(created) + control_plane.BIND_CLOCK_SLACK


def test_binder_window_leaves_a_span_outside_it_pending(setup, monkeypatch, settings):
    """A parent that started before the lookback reads as absent, never bound."""
    scope, _ = setup
    row = occurrence(setup, monkeypatch)
    spans = Spans()
    old = timezone.now() - control_plane.bind_lookback() - timedelta(hours=3)
    spans.put(scope.project_id, span_for(row, start_time=old))
    counts = control_plane.reconcile_tick(span_reader=spans)
    assert (counts["pending"], counts["bound"]) == (1, 0)
    row.refresh_from_db()
    assert row.bound_at is None and row.deleted_at is None
    # A deployment that late-attaches to older traces widens the lookback.
    settings.FI_ATTACHMENTS_BIND_LOOKBACK_HOURS = 24 * 7
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1


def test_batch_retention_equals_retention_for(setup):
    """One lookup per organization derives what retention_for derives."""
    from tracer.services.attachments.authority import retention_for

    scope, _ = setup
    retention = control_plane.Retention()
    for started in (
        retention.now - timedelta(days=3),
        retention.now - timedelta(minutes=5),
    ):
        assert retention(scope.organization_id, started) == retention_for(
            scope.organization_id, started, now=retention.now
        )


def test_claim_is_bounded_per_scope_and_uses_its_index(setup, monkeypatch):
    """Postgres serves each scope's claim window from the partial index."""
    from django.db import connection

    for _ in range(3):
        occurrence(setup, monkeypatch)
    params = {
        "used_scopes": [],
        "used_counts": [],
        "dead_states": ["deleted", "expired"],
        "claimed": [],
        "limit": 256,
        "cap": control_plane.PER_SCOPE_TICK_CAP,
    }
    with connection.cursor() as cursor:
        # Tiny test tables: make the planner show its indexed path.
        for setting in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
            cursor.execute(f"SET LOCAL {setting} = off")
        cursor.execute("EXPLAIN " + control_plane._CLAIM_SQL, params)
        plan = "\n".join(line for (line,) in cursor.fetchall())
    assert "attachment_unbound_claim_idx" in plan, plan

"""Lifecycle: outbox consumers, schedules, sweeps, deletion hooks, schema decisions.

Real PostgreSQL with the 0133 fences. Storage and the ClickHouse span read are
explicit doubles, and ``probe`` stands in for the bubblewrap sandbox (the
sandbox itself is qualified by test_attachment_sandbox.py), so these prove the
drain/sweep wiring and the database rules, not S3 or ClickHouse.
"""

import hashlib
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from tracer.models.attachment import (
    Attachment,
    AttachmentAuditEvent,
    AttachmentBudget,
    AttachmentMediaSession,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentPlaybackGrant,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    AttachmentUsageReceipt,
    AttachmentWriteGrant,
)
from tracer.models.trace import Trace
from tracer.services.attachments import (
    lifecycle,
    occurrences,
    playback,
    retention,
    uploads,
    validation,
)
from tracer.services.attachments.contract import PLACEMENT_FIELDS, AttachmentError
from tracer.services.attachments.probe import probe
from tracer.services.attachments.storage import ObjectIdentity
from tracer.tests import test_attachment_core as core
from tracer.tests import test_attachment_playback as playback_tests
from tracer.tests.test_attachment_core import TRACE, initialized, wav_bytes

setup = core.setup
recording = playback_tests.recording

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


class ErasableStore(core.ImmutableStore):
    """Deterministic server keys, like PrivateMediaStorage, plus exact erase."""

    def __init__(self):
        super().__init__()
        self.erased = []

    def promote(self, snapshot, *, upload_id, expected_length, expected_sha256):
        snapshot.seek(0)
        data = snapshot.read()
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        self.objects[f"original/{upload_id}"] = data
        return ObjectIdentity(f"original/{upload_id}", "immutable-v1")

    def erase_upload(self, upload_id):
        self.erased.append(upload_id)
        for key in (f"quarantine/{upload_id}", f"original/{upload_id}"):
            self.objects.pop(key, None)


@pytest.fixture
def parser_probe(monkeypatch):
    # Explicit parser double, NOT sandbox qualification.
    monkeypatch.setattr(validation, "sandbox_probe", probe)


def streamed(setup, store, *, current=None, **overrides):
    """Initialize, stream and complete one upload; it is then VALIDATING."""
    upload, token, _, info = initialized(setup, current=current, **overrides)
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=store,
    )
    uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)
    return upload, info


def budgets_released():
    return all(
        (row.active_transfers, row.validation_jobs, row.reserved_bytes) == (0, 0, 0)
        for row in AttachmentBudget.objects.all()
    )


def bind(scope, row):
    parent = occurrences.Parent(row.trace_id, row.span_id)
    state = occurrences.SourceState(
        scope.id,
        parent,
        3,
        timezone.now() - timedelta(hours=1),
        timezone.now() + timedelta(hours=1),
        True,
        True,
    )
    return retention.bind_retention(
        scope_id=scope.id, occurrence_id=row.id, source=state
    )


# --- Outbox consumers and schedules -------------------------------------------


def test_every_outbox_event_kind_has_a_registered_consumer():
    kinds = {value for value, _ in AttachmentOutbox._meta.get_field("event").choices}
    assert kinds == set(lifecycle.CONSUMERS)
    assert all(callable(consumer) for consumer in lifecycle.CONSUMERS.values())
    assert set(lifecycle.DRAIN_LIMITS) == kinds


def test_lifecycle_schedules_exist_only_when_enabled(monkeypatch):
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.schedules import attachment_lifecycle as schedules
    from tracer.tasks import attachment_lifecycle as tasks

    monkeypatch.delenv("FI_ATTACHMENTS_ENABLED", raising=False)
    assert schedules.lifecycle_schedules() == []
    monkeypatch.setenv("FI_ATTACHMENTS_ENABLED", "true")
    configured = schedules.lifecycle_schedules()
    assert {c.schedule_id for c in configured} == {
        "attachment-outbox-drain",
        "attachment-lifecycle-sweep",
        "attachment-orphan-sweep",
    }
    assert "tracer.tasks.attachment_lifecycle" in TEMPORAL_ACTIVITY_MODULES
    for config in configured:
        assert config.activity_name in _ACTIVITY_REGISTRY
        assert (
            getattr(tasks, config.activity_name)._activity_name == config.activity_name
        )


def test_activities_are_inert_while_disabled(settings):
    from tracer.tasks import attachment_lifecycle as tasks

    settings.FI_ATTACHMENTS_ENABLED = False
    for name in (
        "drain_attachment_outbox",
        "sweep_attachment_lifecycle",
        "sweep_attachment_orphans",
    ):
        assert getattr(tasks, name)._original_func() == {"disabled": True}


def test_streamed_upload_reaches_ready_through_the_scheduled_drain(
    setup, parser_probe, monkeypatch
):
    from tracer.tasks import attachment_lifecycle as tasks

    store = ErasableStore()
    monkeypatch.setattr(lifecycle, "storage_from_settings", lambda: store)
    upload, _ = streamed(setup, store)
    assert not budgets_released()
    counts = tasks.drain_attachment_outbox._original_func()
    upload.refresh_from_db()
    assert upload.state == AttachmentState.READY
    assert counts["validate_done"] == 1 and counts["admitted_done"] == 1
    assert counts["settled_done"] == 1
    assert budgets_released()
    receipt = AttachmentUsageReceipt.objects.get(upload=upload)
    assert receipt.outcome == AttachmentState.READY
    assert AttachmentAuditEvent.objects.filter(event="admission").count() == 1
    assert not AttachmentOutbox.objects.filter(processed_at__isnull=True).exists()
    # A second tick finds nothing due and writes nothing.
    assert tasks.drain_attachment_outbox._original_func() == {}


def test_drain_never_validates_without_configured_storage(setup, parser_probe):
    store = ErasableStore()
    upload, _ = streamed(setup, store)
    counts = lifecycle.drain_outbox()
    assert counts["validate_skipped_storage_backend_unqualified"] == 1
    upload.refresh_from_db()
    assert upload.state == AttachmentState.VALIDATING
    assert AttachmentOutbox.objects.filter(
        upload=upload, event="validate", processed_at__isnull=True
    ).exists()


def test_without_the_drain_the_project_budget_throttles(setup):
    """Negative control for the next test: undrained uploads hold their slots."""
    store = ErasableStore()
    current, _ = core.session(setup)
    for _ in range(uploads.BUDGETS[2][0]):
        streamed(setup, store, current=current)
    with pytest.raises(AttachmentError, match="throttled"):
        initialized(setup, current=current)


def test_300_sequential_uploads_never_throttle_after_settle(setup, parser_probe):
    """Fable P1-1: slots must come back. The per-minute initialization limiter
    (120/min per project) is a separate rate limit, so the window is advanced
    by one minute every 100 uploads; the slot check is never relaxed."""
    store = ErasableStore()
    current, _ = core.session(setup)
    for index in range(300):
        if index and index % 100 == 0:
            AttachmentBudget.objects.update(
                window_started_at=timezone.now() - timedelta(minutes=1, seconds=1)
            )
        upload, _ = streamed(setup, store, current=current)
        counts = lifecycle.drain_outbox(storage=store)
        assert counts.get("validate_done") == 1, (index, counts)
        assert budgets_released(), index
    assert AttachmentUpload.objects.filter(state=AttachmentState.READY).count() == 300


def test_failing_row_is_pushed_back_and_does_not_stall_the_batch(setup, monkeypatch):
    scope, _ = setup
    first, _, _, _ = initialized(setup)
    second, _, _, _ = initialized(setup)
    rows = list(
        AttachmentOutbox.objects.filter(event="admitted").order_by("available_at", "id")
    )
    real = lifecycle.CONSUMERS["admitted"]

    def flaky(pk, storage):
        if pk == rows[0].pk:
            raise RuntimeError("boom")
        return real(pk, storage)

    monkeypatch.setitem(lifecycle.CONSUMERS, "admitted", flaky)
    counts = lifecycle.drain_outbox(storage=ErasableStore())
    assert counts["admitted_error"] == 1 and counts["admitted_done"] == 1
    rows[0].refresh_from_db()
    assert rows[0].processed_at is None
    assert rows[0].available_at > timezone.now() + timedelta(seconds=30)


# --- Deletion ------------------------------------------------------------------


def test_trace_delete_tombstones_occurrences_and_erase_drain_removes_bytes(
    setup, parser_probe
):
    from tracer.views.trace import _soft_delete_trace_tree

    scope, _ = setup
    store = ErasableStore()
    upload, _ = streamed(setup, store)
    lifecycle.drain_outbox(storage=store)
    bind(scope, upload.occurrence)
    assert f"original/{upload.id}" in store.objects
    trace = Trace.objects.create(id=uuid.UUID(TRACE), project=scope.project, name="t")
    _soft_delete_trace_tree([trace])
    upload.occurrence.refresh_from_db()
    assert upload.occurrence.deleted_at is not None
    assert Attachment.objects.get(pk=upload.attachment_id).state == "deleted"
    counts = lifecycle.drain_outbox(storage=store)
    assert counts["erase_done"] == 1
    assert store.erased == [upload.id] and store.objects == {}


def test_trace_delete_without_attachment_scope_is_a_no_op(project):
    assert (
        retention.tombstone_deleted_traces(
            [SimpleNamespace(project_id=project.id, id=uuid.uuid4())]
        )
        == 0
    )


def test_deletion_hook_failure_never_fails_the_caller(setup, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("boom")

    broken.__name__ = "broken"
    assert retention.run_deletion_hook(broken) == 0


def test_project_delete_fences_and_sweep_tombstones(setup, parser_probe):
    from tracer.models.project import Project
    from tracer.services.project_deletion import soft_delete_projects

    scope, _ = setup
    store = ErasableStore()
    upload, _ = streamed(setup, store)
    lifecycle.drain_outbox(storage=store)
    soft_delete_projects(Project.objects.filter(pk=scope.project_id), "experiment")
    scope.refresh_from_db()
    assert not scope.enabled and scope.deletion_generation == 1
    counts = lifecycle.sweep()
    assert counts["occurrences_fenced"] == 1
    upload.occurrence.refresh_from_db()
    assert upload.occurrence.deleted_at is not None
    assert AttachmentAuditEvent.objects.filter(reason="scope_deleted").count() == 2
    assert lifecycle.drain_outbox(storage=store)["erase_done"] == 1
    assert store.objects == {}


def test_workspace_delete_fences_its_scopes(setup):
    scope, _ = setup
    assert retention.fence_workspace(workspace_id=scope.workspace_id) == 1
    scope.refresh_from_db()
    assert not scope.enabled and scope.deletion_generation == 1


def test_tombstone_parent_loops_past_one_hundred(setup):
    scope, _ = setup
    upload, _, _, _ = initialized(setup)
    for _ in range(150):
        AttachmentOccurrence.objects.create(
            scope=scope,
            client_id=uuid.uuid4(),
            attachment_id=upload.attachment_id,
            capture_session_id=upload.capture_session_id,
            trace_id=upload.occurrence.trace_id,
            span_id=upload.occurrence.span_id,
            direction="mixed",
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
            expires_at=timezone.now() + timedelta(hours=1),
        )
    parent = occurrences.Parent(upload.occurrence.trace_id, upload.occurrence.span_id)
    assert (
        retention.tombstone_parent(
            scope_id=scope.id, parent=parent, source_generation=1
        )
        == 151
    )
    assert not AttachmentOccurrence.objects.filter(deleted_at__isnull=True).exists()


def test_second_tombstone_of_one_attachment_does_not_wedge_the_sweep(recording):
    r = recording
    linked = occurrences.link(
        r.viewer,
        r.authority,
        {
            "source_occurrence_id": str(r.row.client_id),
            "source_parent": {
                "trace_id": str(r.parent.trace_id),
                "span_id": r.parent.span_id,
            },
            "target_parent": {
                "trace_id": str(r.parent.trace_id),
                "span_id": r.parent.span_id,
            },
            "occurrence_id": str(uuid.uuid4()),
            "policy_version": str(r.scope.policy_version),
            "deletion_generation": r.scope.deletion_generation,
        },
    )
    past = timezone.now() - timedelta(seconds=1)
    AttachmentOccurrence.objects.filter(scope=r.scope).update(expires_at=past)
    assert AttachmentOccurrence.objects.filter(scope=r.scope).count() == 2
    assert linked["reference"]["occurrence_id"]
    assert retention.expire_occurrences(scope_id=r.scope.id) == 2
    assert retention.expire_occurrences(scope_id=r.scope.id) == 0
    assert AttachmentOutbox.objects.filter(event="erase").count() == 1


def test_upload_less_tombstone_queues_attachment_keyed_erase_and_erases_it(
    setup, monkeypatch
):
    scope, _ = setup
    upload, _, _, _ = initialized(setup)
    orphan = Attachment.objects.create(
        scope=scope,
        client_id=uuid.uuid4(),
        source_kind="Bytes",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
    )
    row = AttachmentOccurrence.objects.create(
        scope=scope,
        client_id=uuid.uuid4(),
        attachment=orphan,
        capture_session_id=upload.capture_session_id,
        trace_id=upload.occurrence.trace_id,
        span_id=upload.occurrence.span_id,
        direction="mixed",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert retention.expire_occurrences(scope_id=scope.id) == 1
    row.refresh_from_db()
    assert row.deleted_at is not None
    erase = AttachmentOutbox.objects.get(event="erase", upload__isnull=True)
    assert erase.attachment_id == orphan.id
    # An upload-keyed erase row in the same tick is still processed.
    uploads.settle(upload.id, scope_id=scope.id, outcome=AttachmentState.EXPIRED)
    AttachmentOutbox.objects.filter(event="erase").update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    AttachmentOccurrence.objects.filter(pk=upload.occurrence_id).update(
        deleted_at=timezone.now()
    )
    store = ErasableStore()
    counts = lifecycle.drain_outbox(storage=store)
    # Both rows are erased in one tick: the attachment-keyed one by its own key.
    assert counts["erase_done"] == 2
    assert sorted(map(str, store.erased)) == sorted(
        [str(upload.id), str(orphan.client_id)]
    )
    erase.refresh_from_db()
    orphan.refresh_from_db()
    # Repeated once after the horizon (a derivative may still be in flight).
    assert erase.processed_at is None
    assert erase.available_at == orphan.deleted_at + retention.ATTACHMENT_ERASE_HORIZON


# --- Sweeps --------------------------------------------------------------------


def _bound_long_ago(row, *, hours=48):
    AttachmentOccurrence.objects.filter(pk=row.pk).update(
        bound_at=timezone.now() - timedelta(hours=hours)
    )


def _gone(project_id, span_ids):
    return {}


def _no_pg(project_id, rows):
    return set(), set()


def test_orphan_sweep_tombstones_only_on_a_successful_read(recording):
    r = recording
    _bound_long_ago(r.row)
    reads = []

    def failing(project_id, span_ids):
        reads.append(span_ids)
        raise RuntimeError("clickhouse down")

    assert lifecycle.sweep_orphans(span_reader=failing) == {"read_failed": 1}
    r.row.refresh_from_db()
    assert r.row.deleted_at is None and r.row.source_checked_at is None
    assert r.row.source_missing_since is None

    def live(project_id, span_ids):
        return {r.row.span_id: str(r.row.trace_id)}

    assert lifecycle.sweep_orphans(span_reader=live) == {"checked": 1}
    r.row.refresh_from_db()
    assert r.row.deleted_at is None and r.row.source_checked_at is not None

    # First miss: stamped, never tombstoned.
    assert lifecycle.sweep_orphans(span_reader=_gone, pg_reader=_no_pg) == {
        "missing_first": 1
    }
    r.row.refresh_from_db()
    assert r.row.deleted_at is None and r.row.source_missing_since is not None

    # Second miss at least the grace later: tombstoned and the erase is queued.
    erase_before = AttachmentOutbox.objects.filter(event="erase").count()
    later = timezone.now() + lifecycle.orphan_grace() + timedelta(minutes=1)
    counts = lifecycle.sweep_orphans(span_reader=_gone, pg_reader=_no_pg, now=later)
    assert counts == {"orphans_tombstoned": 1}
    r.row.refresh_from_db()
    assert r.row.deleted_at is not None
    assert AttachmentOutbox.objects.filter(event="erase").count() == erase_before + 1
    assert lifecycle.sweep_orphans(span_reader=_gone, pg_reader=_no_pg) == {}


def test_one_empty_read_followed_by_a_live_read_never_tombstones(recording):
    """Review P1 (c7): a lagging or partially restored replica returns one
    successful-but-empty read. The next live read must clear it, so a later
    empty read starts the grace again instead of erasing live media."""
    r = recording
    _bound_long_ago(r.row)
    erase_before = AttachmentOutbox.objects.filter(event="erase").count()

    def live(project_id, span_ids):
        return {r.row.span_id: str(r.row.trace_id)}

    assert lifecycle.sweep_orphans(span_reader=_gone, pg_reader=_no_pg) == {
        "missing_first": 1
    }
    later = timezone.now() + lifecycle.orphan_grace() + timedelta(minutes=1)
    assert lifecycle.sweep_orphans(span_reader=live, now=later) == {"checked": 1}
    r.row.refresh_from_db()
    assert r.row.deleted_at is None and r.row.source_missing_since is None

    # The clock restarted: another empty read, even far later, is only a first miss.
    much_later = later + lifecycle.orphan_grace() * 3
    assert lifecycle.sweep_orphans(
        span_reader=_gone, pg_reader=_no_pg, now=much_later
    ) == {"missing_first": 1}
    r.row.refresh_from_db()
    assert r.row.deleted_at is None
    assert AttachmentOutbox.objects.filter(event="erase").count() == erase_before


def test_two_misses_inside_the_grace_never_tombstone(recording, settings):
    r = recording
    settings.FI_ATTACHMENTS_ORPHAN_GRACE_HOURS = 6
    _bound_long_ago(r.row)
    assert lifecycle.orphan_grace() == timedelta(hours=6)
    lifecycle.sweep_orphans(span_reader=_gone, pg_reader=_no_pg)
    soon = timezone.now() + timedelta(hours=5)
    assert lifecycle.sweep_orphans(span_reader=_gone, pg_reader=_no_pg, now=soon) == {
        "missing_pending": 1
    }
    r.row.refresh_from_db()
    assert r.row.deleted_at is None
    # The grace never drops below one hour, whatever the setting says.
    settings.FI_ATTACHMENTS_ORPHAN_GRACE_HOURS = 0
    assert lifecycle.orphan_grace() == timedelta(hours=1)


def test_tombstone_refuses_a_row_whose_miss_was_cleared(recording):
    """The tombstone re-checks ``source_missing_since`` under the scope lock,
    so a live read between the sweep's read and its tombstone always wins."""
    r = recording
    _bound_long_ago(r.row)
    AttachmentOccurrence.objects.filter(pk=r.row.pk).update(source_missing_since=None)
    assert (
        retention.tombstone_occurrences(
            scope_id=r.scope.id,
            occurrence_ids=[r.row.pk],
            missing_before=timezone.now(),
        )
        == 0
    )
    r.row.refresh_from_db()
    assert r.row.deleted_at is None


def test_freshly_bound_occurrences_are_not_checked(recording):
    r = recording  # bound just now by the fixture
    reads = []

    def recorder(project_id, span_ids):
        reads.append(set(span_ids))
        return {}

    assert lifecycle.sweep_orphans(span_reader=recorder, pg_reader=_no_pg) == {}
    assert reads == []
    r.row.refresh_from_db()
    assert r.row.source_checked_at is None and r.row.deleted_at is None


def test_a_live_postgres_trace_blocks_the_tombstone(recording):
    """ClickHouse may lag or be restored partially; PostgreSQL still holding the
    trace as not deleted means the parent was never deleted."""
    r = recording
    _bound_long_ago(r.row)
    trace = Trace.objects.create(id=r.row.trace_id, project=r.scope.project, name="t")
    later = timezone.now() + lifecycle.orphan_grace() + timedelta(minutes=1)
    for now in (timezone.now(), later, later + lifecycle.orphan_grace() * 2):
        assert lifecycle.sweep_orphans(span_reader=_gone, now=now) == {
            "checked": 1,
            "kept_pg_live": 1,
        }
    r.row.refresh_from_db()
    assert r.row.deleted_at is None and r.row.source_missing_since is None

    # Soft-deleted in PostgreSQL: the two ClickHouse misses decide again.
    Trace.all_objects.filter(pk=trace.pk).update(
        deleted=True, deleted_at=timezone.now()
    )
    assert lifecycle.sweep_orphans(span_reader=_gone, now=later) == {"missing_first": 1}
    final = later + lifecycle.orphan_grace() + timedelta(minutes=1)
    assert lifecycle.sweep_orphans(span_reader=_gone, now=final) == {
        "orphans_tombstoned": 1
    }
    r.row.refresh_from_db()
    assert r.row.deleted_at is not None


def test_a_failed_postgres_check_never_tombstones(recording):
    r = recording
    _bound_long_ago(r.row)
    AttachmentOccurrence.objects.filter(pk=r.row.pk).update(
        source_missing_since=timezone.now() - timedelta(days=30)
    )

    def broken(project_id, rows):
        raise RuntimeError("postgres check failed")

    assert lifecycle.sweep_orphans(span_reader=_gone, pg_reader=broken) == {
        "pg_read_failed": 1
    }
    r.row.refresh_from_db()
    assert r.row.deleted_at is None


def test_a_user_who_viewed_attachments_can_be_deleted(recording):
    """Fable P1-3: a viewer's media sessions and grants, and a write grant they
    made, never block deleting the user (workspace member offboarding)."""
    r = recording
    viewer = User.objects.create_user(
        email=f"viewer-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Viewer",
        organization=r.scope.organization,
    )
    session = AttachmentMediaSession.objects.create(
        user=viewer,
        organization=r.scope.organization,
        application_session_id=uuid.uuid4(),
        token_digest=uuid.uuid4().hex,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    AttachmentPlaybackGrant.objects.create(
        scope=r.scope,
        media_session=session,
        occurrence=r.row,
        token_digest=uuid.uuid4().hex,
        policy_version=r.scope.policy_version,
        deletion_generation=r.scope.deletion_generation,
        source_generation=3,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    key = core.OrgApiKey.no_workspace_objects.create(
        type="ingest",
        organization_id=r.scope.organization_id,
        workspace_id=r.scope.workspace_id,
        simulation_agent_id=uuid.uuid4(),
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    AttachmentWriteGrant.objects.create(
        scope=r.scope,
        api_key=key,
        granted_by=viewer,
        enabled=True,
        expires_at=timezone.now() + timedelta(days=1),
    )
    # Live sessions and grants survive the purge.
    assert lifecycle.purge_media() == 0
    viewer.delete()
    assert not AttachmentMediaSession.objects.filter(pk=session.pk).exists()
    assert not AttachmentPlaybackGrant.objects.filter(media_session=session).exists()
    assert AttachmentWriteGrant.objects.get(api_key=key).granted_by is None
    # The original viewer's session and grant are untouched.
    assert AttachmentPlaybackGrant.objects.filter(media_session=r.session).exists()


def test_expired_media_sessions_and_old_audit_rows_are_purged(recording, settings):
    r = recording
    later = timezone.now() + timedelta(days=2)
    assert lifecycle.purge_media(now=later) >= 2
    assert not AttachmentMediaSession.objects.exists()
    AttachmentAuditEvent.objects.update(created_at=timezone.now() - timedelta(days=401))
    settings.FI_ATTACHMENTS_AUDIT_RETENTION_DAYS = 400
    assert lifecycle.sweep()["audit_purged"] >= 1
    assert not AttachmentAuditEvent.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=400)
    ).exists()
    assert r.scope


# --- Schema decisions ------------------------------------------------------------


def test_placement_is_persisted_emitted_inherited_and_immutable(
    setup, user, parser_probe
):
    scope, _ = setup
    placement = {
        "message_index": 3,
        "part_index": 1,
        "conversation_id": "conv-1",
        "turn_id": "turn_2",
        "tool_call_id": "call-9",
    }
    store = ErasableStore()
    upload, _ = streamed(setup, store, **placement)
    row = AttachmentOccurrence.objects.get(pk=upload.occurrence_id)
    assert {k: getattr(row, k) for k in placement} == placement
    assert row.role is None and row.channel is None
    lifecycle.drain_outbox(storage=store)
    bind(scope, row)
    row.refresh_from_db()
    reference = occurrences.metadata(row, scope, timezone.now())["reference"]
    assert {k: reference[k] for k in placement} == placement
    assert "role" not in reference and "channel" not in reference
    for field, value in (("message_index", 4), ("role", "user"), ("turn_id", "x")):
        with pytest.raises(IntegrityError), transaction.atomic():
            AttachmentOccurrence.objects.filter(pk=row.pk).update(**{field: value})
    # Unchanged placement is not an identity change.
    AttachmentOccurrence.objects.filter(pk=row.pk).update(message_index=3)
    linked_kwargs = {name: getattr(row, name) for name in PLACEMENT_FIELDS}
    assert linked_kwargs["message_index"] == 3


def test_link_inherits_placement(recording):
    r = recording
    target = occurrences.link(
        r.viewer,
        r.authority,
        {
            "source_occurrence_id": str(r.row.client_id),
            "source_parent": {
                "trace_id": str(r.parent.trace_id),
                "span_id": r.parent.span_id,
            },
            "target_parent": {
                "trace_id": str(r.parent.trace_id),
                "span_id": r.parent.span_id,
            },
            "occurrence_id": str(uuid.uuid4()),
            "policy_version": str(r.scope.policy_version),
            "deletion_generation": r.scope.deletion_generation,
        },
    )
    created = AttachmentOccurrence.objects.get(
        client_id=target["reference"]["occurrence_id"]
    )
    source = AttachmentOccurrence.objects.get(pk=r.row.pk)
    assert all(
        getattr(created, name) == getattr(source, name) for name in PLACEMENT_FIELDS
    )


def test_allow_import_is_a_fenced_policy_column(setup):
    scope, _ = setup
    assert scope.allow_import is False
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentScope.objects.filter(pk=scope.pk).update(allow_import=True)
    AttachmentScope.objects.filter(pk=scope.pk).update(
        allow_import=True, policy_version=scope.policy_version + 1
    )
    scope.refresh_from_db()
    assert scope.allow_import is True


def test_attachment_keyed_erase_rows_are_unique(setup):
    scope, _ = setup
    upload, _, _, _ = initialized(setup)
    fields = {
        "scope": scope,
        "attachment_id": upload.attachment_id,
        "event": "erase",
        "generation": 0,
        "available_at": timezone.now(),
    }
    AttachmentOutbox.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentOutbox.objects.create(**fields)
    # The outbox attachment is identity: it cannot be re-pointed later.
    other = Attachment.objects.create(
        scope=scope,
        client_id=uuid.uuid4(),
        source_kind="Bytes",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentOutbox.objects.filter(attachment_id=upload.attachment_id).update(
            attachment=other
        )


def test_upload_outbox_rows_carry_their_attachment(setup):
    upload, _, _, _ = initialized(setup)
    row = AttachmentOutbox.objects.get(upload=upload, event="admitted")
    assert row.attachment_id == upload.attachment_id


# --- Bound media keeps its source retention, not the 24 h pending deadline ------
#
# Regression: bind_retention kept ``min(now + 24 h, source)``, so the scheduled
# sweep tombstoned every bound occurrence a day after upload and the erase
# drain removed its bytes. Time is moved by patching ``timezone.now`` (every
# service reads it through the module), and each read mints a fresh media
# session after the move, so a denial can only come from the occurrence itself.

DECADE = timedelta(days=3650)


def _travel(monkeypatch, delta):
    later = timezone.now() + delta
    monkeypatch.setattr(timezone, "now", lambda: later)
    return later


def _ready_upload(setup, store):
    upload, _ = streamed(setup, store)
    lifecycle.drain_outbox(storage=store)
    upload.refresh_from_db()
    assert upload.state == AttachmentState.READY
    return upload


def _reader(scope, user, row, lifetime):
    parent = occurrences.Parent(row.trace_id, row.span_id)
    state = occurrences.SourceState(
        scope.id,
        parent,
        3,
        timezone.now() - timedelta(hours=1),
        timezone.now() + lifetime,
        True,
        True,
    )
    viewer = occurrences.Viewer(
        user.id,
        uuid.uuid4(),
        scope.organization_id,
        scope.workspace_id,
        scope.project_id,
    )
    return state, playback_tests.AuthorityDouble(viewer, state)


def _plays(authority, row):
    """Resolve then gateway-authorize ``row``: 'ready' or the denial status."""
    viewer = authority.viewer
    _, cookie = playback.exchange(viewer, authority)
    (result,) = playback.resolve(
        viewer,
        authority,
        [
            {
                "occurrence_id": str(row.client_id),
                "parent": {"trace_id": str(row.trace_id), "span_id": row.span_id},
                "representation": "original",
            }
        ],
    )
    if result["status"] != "ready":
        return result["status"]
    grant = result["gateway_path"].rsplit("/", 1)[1]
    try:
        playback.authorize(cookie, grant, row.client_id, authority)
    except AttachmentError as error:
        return f"gateway_{error.code}"
    return "ready"


def _tick(store):
    """The two scheduled jobs, with every queued outbox row made due."""
    swept = lifecycle.sweep()
    AttachmentOutbox.objects.filter(processed_at__isnull=True).update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    return swept, lifecycle.drain_outbox(storage=store)


def test_bound_upload_outlives_the_24h_pending_deadline(
    setup, user, parser_probe, monkeypatch
):
    scope, _ = setup
    store = ErasableStore()
    upload = _ready_upload(setup, store)
    state, authority = _reader(scope, user, upload.occurrence, DECADE)
    row = retention.bind_retention(
        scope_id=scope.id, occurrence_id=upload.occurrence_id, source=state
    )
    assert row.expires_at == state.expires_at == row.source_expires_at
    objects = dict(store.objects)
    assert objects and _plays(authority, row) == "ready"
    _travel(monkeypatch, timedelta(hours=25))
    swept, drained = _tick(store)
    row.refresh_from_db()
    assert swept.get("occurrences_expired", 0) == 0, swept
    assert "erase_done" not in drained, drained
    assert row.deleted_at is None and store.erased == []
    assert store.objects == objects
    assert Attachment.objects.get(pk=upload.attachment_id).state == "ready"
    assert _plays(authority, row) == "ready"
    # A year later it is still served: only the source retention bounds it.
    _travel(monkeypatch, timedelta(days=365))
    _tick(store)
    assert _plays(authority, row) == "ready" and store.objects == objects


def test_linked_occurrence_of_bound_media_outlives_the_24h_deadline(
    setup, user, parser_probe, monkeypatch
):
    scope, _ = setup
    store = ErasableStore()
    upload = _ready_upload(setup, store)
    state, authority = _reader(scope, user, upload.occurrence, DECADE)
    retention.bind_retention(
        scope_id=scope.id, occurrence_id=upload.occurrence_id, source=state
    )
    source = AttachmentOccurrence.objects.get(pk=upload.occurrence_id)
    parent = {"trace_id": str(source.trace_id), "span_id": source.span_id}
    linked = occurrences.link(
        authority.viewer,
        authority,
        {
            "source_occurrence_id": str(source.client_id),
            "source_parent": parent,
            "target_parent": parent,
            "occurrence_id": str(uuid.uuid4()),
            "policy_version": str(scope.policy_version),
            "deletion_generation": scope.deletion_generation,
        },
    )
    created = AttachmentOccurrence.objects.get(
        client_id=linked["reference"]["occurrence_id"]
    )
    assert created.expires_at == state.expires_at
    objects = dict(store.objects)
    _travel(monkeypatch, timedelta(hours=25))
    swept, drained = _tick(store)
    assert swept.get("occurrences_expired", 0) == 0, swept
    assert "erase_done" not in drained and store.objects == objects
    assert _plays(authority, source) == "ready"
    assert _plays(authority, created) == "ready"


def test_unbound_upload_still_expires_and_is_erased_after_24h(
    setup, user, parser_probe, monkeypatch
):
    # Control: the 24 h deadline still applies while nothing has bound it.
    store = ErasableStore()
    upload = _ready_upload(setup, store)
    assert store.objects
    _travel(monkeypatch, timedelta(hours=25))
    swept, drained = _tick(store)
    upload.occurrence.refresh_from_db()
    assert swept["occurrences_expired"] == 1, swept
    assert drained["erase_done"] == 1, drained
    assert upload.occurrence.deleted_at is not None
    assert store.erased == [upload.id] and store.objects == {}
    # And a late bind can no longer revive it.
    scope, _ = setup
    state, _ = _reader(scope, user, upload.occurrence, DECADE)
    with pytest.raises(AttachmentError, match="binding_fenced"):
        retention.bind_retention(
            scope_id=scope.id, occurrence_id=upload.occurrence_id, source=state
        )


def test_bound_upload_with_short_source_retention_expires_with_its_source(
    setup, user, parser_probe, monkeypatch
):
    # Control: binding takes the source retention, it does not disable expiry.
    scope, _ = setup
    store = ErasableStore()
    upload = _ready_upload(setup, store)
    state, authority = _reader(scope, user, upload.occurrence, timedelta(hours=2))
    row = retention.bind_retention(
        scope_id=scope.id, occurrence_id=upload.occurrence_id, source=state
    )
    assert row.expires_at == state.expires_at
    _travel(monkeypatch, timedelta(hours=1))
    _tick(store)
    assert _plays(authority, row) == "ready" and store.erased == []
    _travel(monkeypatch, timedelta(hours=2))
    swept, drained = _tick(store)
    row.refresh_from_db()
    assert swept["occurrences_expired"] == 1, swept
    assert drained["erase_done"] == 1, drained
    assert row.deleted_at is not None and store.objects == {}
    assert _plays(authority, row) == "not_found"

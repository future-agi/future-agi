"""Imported recording segments through binding, deletion, expiry and erase.

An imported segment is its own attachment with one occurrence and no upload
row. These tests drive it end to end through the real binder
(``control_plane.reconcile_tick``), the real DELETE view, the scope fence, the
lifecycle erase drain and the derivative queue, with storage and ClickHouse as
explicit doubles. The sandbox, budgets, fences and triggers are real.
"""

import sys
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from accounts.models import User
from tracer.models.attachment import (
    Attachment,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentState,
)
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentProviderConnection,
    AttachmentRecordingSegment,
    RecordingSegmentState,
)
from tracer.services.attachments import bootstrap as broker
from tracer.services.attachments import (
    contract,
    control_plane,
    derivatives,
    imports,
    lifecycle,
    occurrences,
    provider_recordings,
    retention,
    wire,
)
from tracer.tests import test_attachment_api as http
from tracer.tests.test_attachment_api import api  # noqa: F401  (HTTP fixture)
from tracer.tests.test_attachment_control_plane import Spans
from tracer.tests.test_attachment_recording_imports import (
    KEY,
    SPAN,
    TRACE,
    Store,
    body,
    budgets,
    drive,
    envelope,
    livekit,
    session,
    set_allow_import,
    wav_bytes,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp"),
]

IDLE = (0, 0, 0, 0, 0, 0)


@pytest.fixture(autouse=True)
def unlimited_retention(monkeypatch):
    """Self-hosted retention (no cutoff), so binding has a known expiry."""
    import tfc.ee_gates

    monkeypatch.setattr(
        tfc.ee_gates, "trace_retention_cutoff", lambda organization_id, now=None: None
    )


@pytest.fixture
def rec(api, settings, tmp_path):  # noqa: F811
    settings.FI_ATTACHMENTS_ENVELOPE_KEYS = f"k1:{KEY}"
    settings.FI_ATTACHMENTS_IMPORT_HOSTS = ("media.example.com",)
    settings.FI_ATTACHMENTS_WORK_DIR = str(tmp_path)
    scope = api.scope
    writer = broker.Writer(
        "user", api.user.pk, scope.organization_id, scope.workspace_id
    )
    env = (scope, writer, imports.Importer("user", api.user.pk), api.user)
    return SimpleNamespace(api=api, env=env, scope=scope, store=Store())


def imported(rec, *, seconds=35, channels=2, generate=True):
    data = wav_bytes(seconds=seconds, channels=channels)
    current = session(rec.env)
    _, job = imports.submit(
        rec.env[2],
        scope_id=rec.scope.id,
        body=body(current, data=data, envelope_id=envelope(rec.env, data)),
    )
    assert drive(job, rec.store, data) == "ready"
    segments = list(
        AttachmentRecordingSegment.objects.filter(recording_id=job.recording_id)
        .order_by("index")
    )  # fmt: skip
    if generate:
        for segment in segments:
            derivatives.generate(
                segment.pk, scope_id=segment.scope_id, storage=rec.store
            )
    return job, segments


def keys(segment):
    """Every object the server wrote for one segment: original + derivatives."""
    return {f"original/{segment.attachment_client_id}"} | {
        f"original/{derivatives.derivative_id(segment, representation)}"
        for representation in derivatives.REPRESENTATIONS
    }


def parent_spans(rec):
    """The import's ``association.parent`` as a live ClickHouse span."""
    spans = Spans()
    spans.put(
        rec.scope.project_id,
        SimpleNamespace(
            id=SPAN,
            trace_id=str(uuid.UUID(TRACE)),
            project_id=str(rec.scope.project_id),
            # Equal to what the DELETE view's LiveAuthority derives.
            start_time=rec.api.state.started_at,
            is_deleted=0,
            attrs_string={},
        ),
    )
    return spans


def occurrence_of(segment):
    return AttachmentOccurrence.objects.get(
        scope_id=segment.scope_id, client_id=segment.occurrence_client_id
    )


def drain_due(rec):
    AttachmentOutbox.objects.filter(event="erase", processed_at__isnull=True).update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    return lifecycle.drain_outbox(storage=rec.store)


def test_imported_segments_bind_on_the_association_parent(rec):
    _, segments = imported(rec, generate=False)
    counts = control_plane.reconcile_tick(span_reader=parent_spans(rec))
    assert counts["bound"] == len(segments) == 2
    for segment in segments:
        row = occurrence_of(segment)
        assert row.bound_at is not None and row.source_generation == 0
        assert (row.trace_id, row.span_id) == (uuid.UUID(TRACE), SPAN)


def test_view_delete_of_a_bound_imported_segment_erases_it_and_its_derivatives(
    rec, monkeypatch
):
    job, (first, second) = imported(rec)
    assert keys(first) | keys(second) <= set(rec.store.objects)
    assert control_plane.reconcile_tick(span_reader=parent_spans(rec))["bound"] == 2

    reply = rec.api.client.delete(
        f"{http.base(rec.api)}/occurrences/{first.occurrence_client_id}"
        f"?trace_id={TRACE}&span_id={SPAN}",
        **http.auth(rec.api.token),
    )
    http.ok(reply, 204)
    assert occurrence_of(first).deleted_at is not None
    attachment = Attachment.objects.get(
        scope=rec.scope, client_id=first.attachment_client_id
    )
    assert attachment.state == AttachmentState.DELETED
    erase = AttachmentOutbox.objects.get(
        event="erase", upload__isnull=True, attachment=attachment
    )

    counts = drain_due(rec)
    assert counts.get("erase_done") == 1, counts
    assert not keys(first) & set(rec.store.objects)
    assert keys(second) <= set(rec.store.objects)  # the survivor is untouched
    first.refresh_from_db()
    assert first.state == RecordingSegmentState.ERASED
    assert derivatives.identity_for(
        scope_id=rec.scope.id,
        occurrence_id=first.occurrence_client_id,
        representation="peaks",
    ) is None  # fmt: skip
    # Repeated once after the horizon: a derivative promoted by a producer that
    # died before its own cleanup is still erased.
    erase.refresh_from_db()
    assert erase.processed_at is None
    assert (
        erase.available_at == attachment.deleted_at + retention.ATTACHMENT_ERASE_HORIZON
    )
    late = f"original/{derivatives.derivative_id(first, 'peaks')}"
    rec.store.objects[late] = b"late"
    monkeypatch.setattr(retention, "ATTACHMENT_ERASE_HORIZON", timedelta(0))
    assert drain_due(rec).get("erase_done") == 1
    erase.refresh_from_db()
    assert erase.processed_at is not None and late not in rec.store.objects

    # The manifest shows the deleted segment as an incomplete gap.
    manifest = http.ok(
        rec.api.client.get(
            f"{http.base(rec.api)}/recordings/{job.recording.client_id}/manifest",
            {"trace_id": TRACE, "span_id": SPAN},
            **http.auth(rec.api.token),
        ),
        200,
        "RecordingManifest",
    )
    assert [s["occurrence_id"] for s in manifest["segments"]] == [
        str(second.occurrence_client_id)
    ]
    assert manifest["capture_complete"] is False


def _travel(monkeypatch, delta):
    """Move every service's clock (they all read ``timezone.now``)."""
    later = timezone.now() + delta
    monkeypatch.setattr(timezone, "now", lambda: later)
    return later


def _scheduled_ticks(rec):
    """Every scheduled job that can expire, purge or erase, all rows due."""
    control_plane.reconcile_tick(span_reader=parent_spans(rec))
    swept = lifecycle.sweep()
    purged = derivatives.purge(limit=1000)
    imports.sweep(storage=rec.store)
    AttachmentOutbox.objects.filter(processed_at__isnull=True).update(
        available_at=timezone.now() - timedelta(seconds=1)
    )
    return swept, purged, lifecycle.drain_outbox(storage=rec.store)


def test_bound_imported_recording_keeps_playing_after_the_24h_deadline(
    rec, monkeypatch
):
    """Regression: binding kept the import's 24 h pending deadline, so the
    sweep tombstoned every imported segment a day later and the drain erased
    its original and derivatives. The real binder, sweeps, drain and HTTP
    manifest read; only the clock, storage and ClickHouse are doubles."""
    job, segments = imported(rec)
    written = set().union(*(keys(segment) for segment in segments))
    assert written <= set(rec.store.objects)
    assert control_plane.reconcile_tick(span_reader=parent_spans(rec))["bound"] == 2
    for segment in segments:
        row = occurrence_of(segment)
        assert row.expires_at == row.source_expires_at
        assert row.expires_at - row.created_at > timedelta(days=365)
    for hours in (25, 24 * 30):
        _travel(monkeypatch, timedelta(hours=hours))
        swept, purged, drained = _scheduled_ticks(rec)
        assert swept.get("occurrences_expired", 0) == 0, (hours, swept)
        assert "erase_done" not in drained, (hours, drained)
        assert written <= set(rec.store.objects), hours
        for segment in segments:
            segment.refresh_from_db()
            assert segment.state != RecordingSegmentState.ERASED
            assert occurrence_of(segment).deleted_at is None
        manifest = http.ok(
            rec.api.client.get(
                f"{http.base(rec.api)}/recordings/{job.recording.client_id}/manifest",
                {"trace_id": TRACE, "span_id": SPAN},
                **http.auth(http.issue_tokens(rec.api.user)["access"]),
            ),
            200,
            "RecordingManifest",
        )
        assert [s["occurrence_id"] for s in manifest["segments"]] == [
            str(segment.occurrence_client_id) for segment in segments
        ]
    assert not AttachmentOutbox.objects.filter(event="erase").exists()


def test_unbound_imported_segments_still_expire_after_the_24h_deadline(
    rec, monkeypatch
):
    """Control: with no parent span nothing binds, and the deadline holds."""
    _, segments = imported(rec)
    assert control_plane.reconcile_tick(span_reader=Spans())["pending"] == 2
    _travel(monkeypatch, timedelta(hours=25))
    control_plane.reconcile_tick(span_reader=Spans())
    lifecycle.sweep()
    assert all(occurrence_of(segment).deleted_at for segment in segments)
    assert drain_due(rec).get("erase_done") == 2
    for segment in segments:
        assert not keys(segment) & set(rec.store.objects)


def test_expired_imported_segments_are_retired_by_reconcile_tick_and_erased(rec):
    _, segments = imported(rec)
    AttachmentOccurrence.objects.filter(scope=rec.scope).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    counts = control_plane.reconcile_tick(span_reader=Spans())
    assert counts["expired"] == 2 and counts["conflict"] == 0
    assert all(occurrence_of(segment).deleted_at for segment in segments)
    # No wedge: nothing is claimed again.
    assert sum(control_plane.reconcile_tick(span_reader=Spans()).values()) == 0
    assert drain_due(rec).get("erase_done") == 2
    for segment in segments:
        assert not keys(segment) & set(rec.store.objects)


def test_expiry_sweep_and_tombstone_parent_handle_imported_segments(rec):
    _, segments = imported(rec, generate=False)
    AttachmentOccurrence.objects.filter(pk=occurrence_of(segments[0]).pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert retention.expire_occurrences(scope_id=rec.scope.id) == 1
    parent = occurrences.Parent(uuid.UUID(TRACE), SPAN)
    assert (
        retention.tombstone_parent(
            scope_id=rec.scope.id, parent=parent, source_generation=0
        )
        == 1
    )
    assert all(occurrence_of(segment).deleted_at for segment in segments)
    assert AttachmentOutbox.objects.filter(
        event="erase", upload__isnull=True
    ).count() == 2  # fmt: skip


def test_fencing_the_scope_erases_imported_segments_and_derivatives(rec):
    _, segments = imported(rec)
    retention.fence_scope(scope_id=rec.scope.id)
    assert retention.tombstone_fenced(scope_id=rec.scope.id) == 2
    assert drain_due(rec).get("erase_done") == 2
    for segment in segments:
        assert not keys(segment) & set(rec.store.objects)
        segment.refresh_from_db()
        assert segment.state == RecordingSegmentState.ERASED


def test_derivative_queue_skips_dead_segments_and_rotates_failures(rec):
    old, old_segments = imported(rec, seconds=65, channels=1, generate=False)
    AttachmentOccurrence.objects.filter(recording_id=old.recording_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    _, (a,) = imported(rec, seconds=10, channels=1, generate=False)
    _, (b,) = imported(rec, seconds=10, channels=1, generate=False)
    queued = [pk for pk, _ in derivatives.pending(limit=20)]
    assert queued == [a.pk, b.pk]  # expired segments never occupy the queue
    derivatives.defer(a.pk)
    assert [pk for pk, _ in derivatives.pending(limit=20)] == [b.pk, a.pk]
    assert [pk for pk, _ in derivatives.pending(limit=1)] == [b.pk]
    derivatives.generate(b.pk, scope_id=b.scope_id, storage=rec.store)
    assert [pk for pk, _ in derivatives.pending(limit=20)] == [a.pk]
    assert len(old_segments) == 3


def test_a_derivative_fenced_mid_production_is_not_left_in_storage(rec, monkeypatch):
    _, (segment,) = imported(rec, seconds=10, channels=1, generate=False)
    real = derivatives._publish

    def deleted_meanwhile(*args, **kwargs):
        AttachmentOccurrence.objects.filter(pk=occurrence_of(segment).pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(derivatives, "_publish", deleted_meanwhile)
    with pytest.raises(contract.AttachmentError, match="validation_fenced"):
        derivatives.generate(segment.pk, scope_id=segment.scope_id, storage=rec.store)
    assert f"original/{derivatives.derivative_id(segment, 'peaks')}" not in (
        rec.store.objects
    )


def test_a_failed_release_after_publication_is_retried_not_leaked(rec, monkeypatch):
    real = imports._release
    calls = {"n": 0}

    def flaky(job):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("transient db error")
        return real(job)

    monkeypatch.setattr(imports, "_release", flaky)
    data = wav_bytes(seconds=35)
    current = session(rec.env)
    _, job = imports.submit(
        rec.env[2],
        scope_id=rec.scope.id,
        body=body(current, data=data, envelope_id=envelope(rec.env, data)),
    )
    # The release after publication fails, and so does run()'s own retry.
    assert drive(job, rec.store, data) == "ready"
    job.refresh_from_db()
    assert (job.state, job.reason, job.released_at) == ("ready", "ready", None)
    assert set(budgets().values()) != {IDLE}
    # The sweep picks it up; the public status still says ready.
    assert imports.sweep(storage=rec.store) == 1
    job.refresh_from_db()
    assert job.released_at is not None
    assert set(budgets().values()) == {IDLE}
    assert imports.status(rec.env[2], scope_id=rec.scope.id, job_id=job.id) == {
        "job_id": str(job.id),
        "state": "ready",
        "reason": "ready",
    }
    assert imports.sweep(storage=rec.store) == 0  # exactly once


def test_a_single_failed_release_is_retried_inside_the_run(rec, monkeypatch):
    real = imports._release
    calls = {"n": 0}

    def flaky(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db error")
        return real(job)

    monkeypatch.setattr(imports, "_release", flaky)
    data = wav_bytes(seconds=35)
    current = session(rec.env)
    _, job = imports.submit(
        rec.env[2],
        scope_id=rec.scope.id,
        body=body(current, data=data, envelope_id=envelope(rec.env, data)),
    )
    assert drive(job, rec.store, data) == "ready"
    job.refresh_from_db()
    assert job.released_at is not None and set(budgets().values()) == {IDLE}


def test_staged_segments_reserve_an_object_and_a_link_each(rec, monkeypatch):
    seen = {}
    real = imports._publish

    def observe(job, *args, **kwargs):
        seen.update(budgets())
        return real(job, *args, **kwargs)

    monkeypatch.setattr(imports, "_publish", observe)
    data = wav_bytes(seconds=65)  # three segments
    current = session(rec.env)
    _, job = imports.submit(
        rec.env[2],
        scope_id=rec.scope.id,
        body=body(current, data=data, envelope_id=envelope(rec.env, data)),
    )
    assert drive(job, rec.store, data) == "ready"
    job.refresh_from_db()
    assert job.reserved_segments == 3
    charged = {key: value for key, value in seen.items() if value != IDLE}
    # Imports use the deployment import row, never an upload shard.
    assert set(charged) == {
        "global:imports",
        f"org:{rec.scope.organization_id}",
        f"project:{rec.scope.project_id}",
    }
    for value in charged.values():
        _, transfers, recordings, validation_jobs, objects, links = value
        assert (transfers, recordings, validation_jobs) == (1, 1, 1)
        assert (objects, links) == (2 + 3, 1 + 3)
    assert set(budgets().values()) == {IDLE}


def test_policy_advertises_import_sources_only_while_imports_are_on(rec, monkeypatch):
    # A deployment that mounts the producing routes registers their kinds.
    monkeypatch.setattr(
        contract, "IMPORT_SOURCE_PRODUCERS", {"RemoteUrl", "ProviderFile"}
    )
    scope = rec.scope
    scope.refresh_from_db()
    off = contract.policy_document(scope)
    wire.validate("Policy", off)
    assert "RemoteUrl" not in off["allowed_sources"]
    set_allow_import(scope, True)
    scope.refresh_from_db()
    on = contract.policy_document(scope)
    wire.validate("Policy", on)
    assert "RemoteUrl" in on["allowed_sources"]
    # No provider output reader is qualified, so ProviderFile is not offered.
    assert "ProviderFile" not in on["allowed_sources"]
    monkeypatch.setitem(provider_recordings.READERS, "livekit", object())
    with_reader = contract.policy_document(scope)
    wire.validate("Policy", with_reader)
    assert {"RemoteUrl", "ProviderFile"} <= set(with_reader["allowed_sources"])


def test_deleting_the_connection_creator_keeps_the_connection(rec):
    creator = User.objects.create_user(
        email=f"creator-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Creator",
        organization=rec.scope.organization,
    )
    scope, writer, importer, _ = rec.env
    connection = livekit((scope, writer, importer, creator))
    creator.delete()
    connection = AttachmentProviderConnection.objects.get(pk=connection.pk)
    assert connection.created_by_id is None and connection.enabled
    assert not AttachmentImportJob.objects.exists()

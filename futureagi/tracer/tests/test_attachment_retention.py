"""Retention/accounting components. Real PG; no enterprise or S3 qualification."""

import io
import uuid
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentAuditEvent,
    AttachmentBlob,
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentState,
    AttachmentUsageReceipt,
    UploadReservation,
)
from tracer.services.attachments import accounting, retention, uploads, validation
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.occurrences import Parent, SourceState
from tracer.services.attachments.probe import probe
from tracer.services.attachments.storage import ObjectIdentity
from tracer.tests import test_attachment_core as core
from tracer.tests import test_attachment_playback as playback_tests
from tracer.tests.test_attachment_core import ImmutableStore, initialized, wav_bytes
from tracer.tests.test_attachment_playback import response
from tracer.tests.test_attachment_storage import private_client, storage

setup = core.setup
recording = playback_tests.recording

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_verified_usage_is_settled_once_even_after_delete(recording):
    r = recording
    event = AttachmentOutbox.objects.get(upload=r.upload, event="settled")
    retention.delete_occurrence(
        r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
    )
    first = accounting.consume_settlement(event.id)
    assert first.stored_bytes == len(wav_bytes()) and first.outcome == "ready"
    assert accounting.consume_settlement(event.id).id == first.id
    assert AttachmentUsageReceipt.objects.count() == 1
    assert AttachmentAuditEvent.objects.filter(event="settlement").count() == 1
    assert all(
        row.reserved_bytes == 0 and row.active_transfers == 0
        for row in AttachmentBudget.objects.all()
    )


def test_expired_reservations_release_once_and_store_zero_bytes(setup):
    upload, _, _, _ = initialized(setup)
    UploadReservation.objects.filter(upload=upload).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert accounting.expire_reservations() == 1
    assert accounting.expire_reservations() == 0
    event = AttachmentOutbox.objects.get(upload=upload, event="settled")
    receipt = accounting.consume_settlement(event.id)
    assert receipt.stored_bytes == 0 and receipt.outcome == "expired"
    assert all(
        row.reserved_bytes == 0
        and row.active_transfers == 0
        and row.validation_jobs == 0
        for row in AttachmentBudget.objects.all()
    )


def test_audit_schema_rejects_content_and_conflicting_idempotency(recording):
    r = recording
    args = {
        "scope": r.scope,
        "actor_id": r.viewer.user_id,
        "entity_id": r.row.id,
        "event": "deletion",
        "reason": "requested",
        "event_id": uuid.uuid4(),
    }
    first = accounting.audit(**args)
    assert accounting.audit(**args).id == first.id
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        accounting.audit(**{**args, "reason": "retention"})
    for change in [
        {"reason": "https://signed.invalid/CANARY"},
        {"event": "speaker-name"},
        {"entity_id": "secret-file.wav"},
    ]:
        with pytest.raises(AttachmentError, match="invalid_audit_event"):
            accounting.audit(**{**args, **change})
    with pytest.raises(TypeError):
        accounting.audit(**args, filename="SECRET")


def test_delete_before_binding_cannot_late_bind_or_transfer(setup):
    scope, _ = setup
    upload, token, _, _ = initialized(setup)
    parent = Parent(upload.occurrence.trace_id, upload.occurrence.span_id)
    source = SourceState(
        scope.id,
        parent,
        1,
        timezone.now() - timedelta(hours=1),
        timezone.now() + timedelta(hours=1),
        True,
    )
    assert (
        retention.tombstone_parent(
            scope_id=scope.id, parent=parent, source_generation=1
        )
        == 1
    )
    with pytest.raises(AttachmentError, match="binding_fenced"):
        retention.bind_retention(
            scope_id=scope.id, occurrence_id=upload.occurrence_id, source=source
        )
    store = ImmutableStore()
    with pytest.raises(AttachmentError):
        uploads.transfer(
            token,
            scope_id=scope.id,
            upload_id=upload.id,
            chunks=[wav_bytes()],
            storage=store,
        )
    assert store.objects == {}


@pytest.mark.parametrize("during", ["delete", "expiry", "scope"])
def test_validation_rechecks_tombstone_retention_and_generation(
    setup, monkeypatch, during
):
    scope, _ = setup
    upload, token, _, _ = initialized(setup)
    store = ImmutableStore()
    uploads.transfer(
        token,
        scope_id=scope.id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=store,
    )
    uploads.complete(token, scope_id=scope.id, upload_id=upload.id)

    def probing(path):
        if during == "delete":
            retention.tombstone_parent(
                scope_id=scope.id,
                parent=Parent(upload.occurrence.trace_id, upload.occurrence.span_id),
                source_generation=1,
            )
        elif during == "expiry":
            AttachmentOccurrence.objects.filter(pk=upload.occurrence_id).update(
                source_expires_at=timezone.now() - timedelta(seconds=1)
            )
        else:
            retention.fence_scope(scope_id=scope.id)
        return probe(path)

    monkeypatch.setattr(validation, "sandbox_probe", probing)
    with pytest.raises(AttachmentError):
        validation.validate(upload.id, scope_id=scope.id, storage=store)
    assert store.promotions == [] and not AttachmentBlob.objects.exists()


def test_known_retention_cannot_be_extended_and_unknown_denies(setup):
    scope, _ = setup
    upload, _, _, _ = initialized(setup)
    parent = Parent(upload.occurrence.trace_id, upload.occurrence.span_id)
    state = SourceState(
        scope.id,
        parent,
        1,
        timezone.now() - timedelta(hours=1),
        timezone.now() + timedelta(minutes=2),
        True,
    )
    for bad in (
        replace(state, expires_at=None),
        replace(state, scope_id=uuid.uuid4()),
        replace(state, expires_at=timezone.now() - timedelta(seconds=1)),
    ):
        with pytest.raises(AttachmentError, match="binding_fenced"):
            retention.bind_retention(
                scope_id=scope.id, occurrence_id=upload.occurrence_id, source=bad
            )
    row = retention.bind_retention(
        scope_id=scope.id, occurrence_id=upload.occurrence_id, source=state
    )
    assert row.expires_at == state.expires_at
    assert (
        retention.bind_retention(
            scope_id=scope.id, occurrence_id=row.id, source=state
        ).id
        == row.id
    )
    with pytest.raises(AttachmentError, match="binding_fenced"):
        retention.bind_retention(
            scope_id=scope.id,
            occurrence_id=row.id,
            source=replace(state, expires_at=state.expires_at + timedelta(hours=1)),
        )


def test_source_tombstone_generation_and_scope_are_exact(recording):
    r = recording
    assert (
        retention.tombstone_parent(
            scope_id=r.scope.id, parent=r.parent, source_generation=2
        )
        == 0
    )
    assert response(r, method="HEAD").status_code == 200
    assert (
        retention.tombstone_parent(
            scope_id=r.scope.id,
            parent=replace(r.parent, span_id="1234567890abcdef"),
            source_generation=3,
        )
        == 0
    )
    assert (
        retention.tombstone_parent(
            scope_id=r.scope.id, parent=r.parent, source_generation=3
        )
        == 1
    )
    assert (
        retention.tombstone_parent(
            scope_id=r.scope.id, parent=r.parent, source_generation=3
        )
        == 0
    )
    assert response(r, method="HEAD").status_code == 404


def test_one_occurrence_delete_preserves_separate_survivor(recording):
    r = recording
    original = AttachmentOccurrence.objects.get(pk=r.row.id)
    survivor = AttachmentOccurrence.objects.create(
        scope=r.scope,
        client_id=uuid.uuid4(),
        attachment=original.attachment,
        capture_session=original.capture_session,
        trace_id=original.trace_id,
        span_id=original.span_id,
        direction=original.direction,
        policy_version=original.policy_version,
        deletion_generation=original.deletion_generation,
        bound_at=original.bound_at,
        source_started_at=original.source_started_at,
        source_expires_at=original.source_expires_at,
        source_generation=original.source_generation,
        expires_at=original.expires_at,
    )  # Explicit fixture insertion, NOT a relink API qualification.
    retention.delete_occurrence(
        r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
    )
    r.upload.attachment.refresh_from_db()
    assert r.upload.attachment.state == AttachmentState.READY
    assert not AttachmentOutbox.objects.filter(upload=r.upload, event="erase").exists()
    from tracer.services.attachments.occurrences import get_occurrence

    assert (
        get_occurrence(r.viewer, r.authority, survivor.client_id, r.parent, ready=True)[
            0
        ].id
        == survivor.id
    )
    retention.delete_occurrence(
        r.viewer, r.authority, occurrence_id=survivor.client_id, parent=r.parent
    )
    r.upload.attachment.refresh_from_db()
    assert r.upload.attachment.state == AttachmentState.DELETED


def test_erase_replays_until_transfer_horizon_and_retries_failure(
    recording, monkeypatch
):
    r = recording
    retention.delete_occurrence(
        r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
    )
    event = AttachmentOutbox.objects.get(upload=r.upload, event="erase")
    service = Mock()
    service.erase_upload.side_effect = AttachmentError("cleanup_unavailable", 503)
    with pytest.raises(AttachmentError):
        retention.erase(event.id, storage=service)
    event.refresh_from_db()
    assert event.processed_at is None and event.attempts == 0
    service.erase_upload.side_effect = None
    assert retention.erase(event.id, storage=service)
    event.refresh_from_db()
    assert event.processed_at is None and event.attempts == 1
    after = r.upload.capture_session.expires_at + timedelta(minutes=2)
    monkeypatch.setattr(retention.timezone, "now", lambda: after)
    assert retention.erase(event.id, storage=service)
    assert not retention.erase(event.id, storage=service)
    event.refresh_from_db()
    assert event.processed_at == after
    assert service.erase_upload.call_count == 3
    assert service.erase_upload.call_args.args == (r.upload.id,)


def test_expiry_tombstones_without_extending_upload_time(recording):
    r = recording
    AttachmentOccurrence.objects.filter(pk=r.row.id).update(
        source_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert retention.expire_occurrences(scope_id=r.scope.id) == 1
    assert retention.expire_occurrences(scope_id=r.scope.id) == 0
    assert response(r, method="HEAD").status_code == 404


def test_storage_range_pins_version_and_closes_bad_reply():
    client = private_client()
    service = storage(client)
    body = io.BytesIO(b"abc")
    client.get_object.return_value = {
        "Body": body,
        "VersionId": "v1",
        "ContentLength": 3,
        "ContentRange": "bytes 2-4/10",
    }
    assert (
        service.open_range(
            ObjectIdentity("original/id", "v1"), start=2, end=4, expected_length=10
        )
        is body
    )
    assert client.get_object.call_args.kwargs["VersionId"] == "v1"
    body.close()
    for change in (
        {"VersionId": "other"},
        {"ContentLength": 10},
        {"ContentRange": "bytes 0-9/10"},
    ):
        body = io.BytesIO(b"abc")
        client.get_object.return_value = {
            "Body": body,
            "VersionId": "v1",
            "ContentLength": 3,
            "ContentRange": "bytes 2-4/10",
            **change,
        }
        with pytest.raises(AttachmentError, match="media_unavailable"):
            service.open_range(
                ObjectIdentity("original/id", "v1"), start=2, end=4, expected_length=10
            )
        assert body.closed


def test_cleanup_only_exact_keys_all_versions_markers_and_parts():
    client = private_client()
    upload_id = uuid.uuid4()

    def paginator(name):
        def pages(**kwargs):
            key = kwargs["Prefix"]
            if name == "list_object_versions":
                return [
                    {
                        "Versions": [
                            {"Key": key, "VersionId": "v1"},
                            {"Key": key + "-foreign", "VersionId": "foreign"},
                        ],
                        "DeleteMarkers": [{"Key": key, "VersionId": "marker"}],
                    },
                    {"Versions": [{"Key": key, "VersionId": "v2"}]},
                ]
            return [
                {
                    "Uploads": [
                        {"Key": key, "UploadId": "part"},
                        {"Key": key + "-foreign", "UploadId": "foreign"},
                    ]
                }
            ]

        return Mock(paginate=pages)

    client.get_paginator.side_effect = paginator
    storage(client).erase_upload(upload_id)
    assert client.delete_object.call_count == 6
    assert client.abort_multipart_upload.call_count == 2
    assert all(
        call.kwargs["Key"].endswith(str(upload_id))
        for call in client.delete_object.call_args_list
    )
    client.get_paginator.side_effect = RuntimeError("secret-signed-url-CANARY")
    with pytest.raises(AttachmentError, match="^cleanup_unavailable$"):
        storage(client).erase_upload(upload_id)

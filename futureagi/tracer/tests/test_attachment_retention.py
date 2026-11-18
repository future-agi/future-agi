"""Retention/accounting components. Real PG; no enterprise or S3 qualification."""

import io
import uuid
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentAuditEvent,
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentState,
    AttachmentUpload,
)
from tracer.services.attachments import accounting, retention, uploads, validation
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import ObjectIdentity, ObjectLocator
from tracer.services.attachments.occurrences import Parent, SourceState
from tracer.services.attachments.probe import probe
from tracer.tests import test_attachment_core as core
from tracer.tests import test_attachment_playback as playback_tests
from tracer.tests.attachment_fixtures import (
    BINDING_ID,
    erase_terminal_upload,
    legacy_settlement_event,
)
from tracer.tests.test_attachment_core import ImmutableStore, initialized, wav_bytes
from tracer.tests.test_attachment_playback import response
from tracer.tests.test_attachment_storage import private_client, storage

setup = core.setup
recording = playback_tests.recording

pytestmark = [pytest.mark.unit]


@pytest.mark.django_db(transaction=True)
def test_verified_usage_is_settled_once_even_after_delete(recording, monkeypatch):
    r = recording
    event = legacy_settlement_event(r.upload)
    original_receipt = (r.upload.usage_id, r.upload.usage_created_at)
    original_blob = (
        r.upload.attachment.verified_blob_id,
        r.upload.attachment.verified_blob_created_at,
    )
    retention.delete_occurrence(
        r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
    )
    first = accounting.consume_settlement(event.id)
    assert (
        first.usage_stored_bytes == len(wav_bytes()) and first.usage_outcome == "ready"
    )
    assert accounting.consume_settlement(event.id).usage_id == first.usage_id
    assert AttachmentUpload.objects.filter(usage_id__isnull=False).count() == 1
    assert AttachmentAuditEvent.objects.filter(event="settlement").count() == 1
    assert (first.usage_id, first.usage_created_at) == original_receipt
    first.attachment.refresh_from_db()
    assert (
        first.attachment.verified_blob_id,
        first.attachment.verified_blob_created_at,
    ) == original_blob
    erase_terminal_upload(r.upload, r.write_store, monkeypatch)
    assert all(
        row.reserved_bytes == 0 and row.active_transfers == 0
        for row in AttachmentBudget.objects.all()
    )


@pytest.mark.django_db(transaction=True)
def test_expired_reservations_release_once_and_store_zero_bytes(setup, monkeypatch):
    upload, _, _, _ = initialized(setup)
    # Expiry identity is write-once. Advance the clock, never rewrite the ledger.
    after = upload.reservation_expires_at + timedelta(seconds=1)
    monkeypatch.setattr(timezone, "now", lambda: after)
    assert accounting.expire_reservations() == 1
    assert accounting.expire_reservations() == 0
    event = legacy_settlement_event(upload)
    receipt = accounting.consume_settlement(event.id)
    assert receipt.usage_stored_bytes == 0 and receipt.usage_outcome == "expired"
    erase_terminal_upload(upload, ImmutableStore(), monkeypatch)
    assert all(
        row.reserved_bytes == 0
        and row.active_transfers == 0
        and row.validation_jobs == 0
        for row in AttachmentBudget.objects.all()
    )


@pytest.mark.django_db
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


@pytest.mark.django_db
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
@pytest.mark.django_db
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
    assert (
        store.promotions == []
        and not Attachment.objects.filter(verified_blob_id__isnull=False).exists()
    )


@pytest.mark.django_db
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


@pytest.mark.django_db
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


@pytest.mark.django_db
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


@pytest.mark.django_db(transaction=True)
def test_erase_replays_until_transfer_horizon_and_retries_failure(
    recording, monkeypatch
):
    r = recording
    retention.delete_occurrence(
        r.viewer, r.authority, occurrence_id=r.row.client_id, parent=r.parent
    )
    event = AttachmentOutbox.objects.get(upload=r.upload, event="erase")
    r.upload.refresh_from_db()
    r.upload.attachment.refresh_from_db()
    service = r.write_store
    erase_keys = Mock(wraps=service.erase_all_versions)
    monkeypatch.setattr(service, "erase_all_versions", erase_keys)
    early = event.available_at
    monkeypatch.setattr(timezone, "now", lambda: early)
    erase_keys.side_effect = AttachmentError("cleanup_unavailable", 503)
    with pytest.raises(AttachmentError, match="cleanup_unavailable"):
        retention.erase(event.id, storage=service)
    event.refresh_from_db()
    assert event.processed_at is None and event.attempts == 0
    erase_keys.side_effect = None
    # Successful early emptiness is not completion or permission to release.
    assert not retention.erase(event.id, storage=service)
    event.refresh_from_db()
    assert event.processed_at is None and event.attempts == 1
    r.upload.refresh_from_db()
    assert r.upload.reservation_settled_at is None
    after = max(
        event.available_at,
        r.upload.expires_at + retention.TRANSFER_DRAIN,
        r.upload.cleanup_not_before,
        r.upload.attachment.deleted_at + retention.ATTACHMENT_ERASE_HORIZON,
    ) + timedelta(seconds=1)
    monkeypatch.setattr(timezone, "now", lambda: after)
    # Real consumer retires the accepted validation claim before final erasure.
    for pending in AttachmentOutbox.objects.filter(
        upload=r.upload, event="validate", processed_at__isnull=True
    ):
        assert validation.consume_validation(pending.id, storage=service)
    assert retention.erase(event.id, storage=service)
    assert not retention.erase(event.id, storage=service)
    event.refresh_from_db()
    r.upload.refresh_from_db()
    assert event.processed_at == after
    assert erase_keys.call_count == 5  # failed first key, two early, two final
    assert {call.args[0] for call in erase_keys.call_args_list} == {
        ObjectLocator(service.binding_id, "quarantine", r.upload.quarantine_key),
        ObjectLocator(service.binding_id, "serving", r.upload.serving_key),
    }
    assert r.upload.reservation_settled_at is not None and service.objects == {}


@pytest.mark.django_db
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
        "ServerSideEncryption": "AES256",
    }
    assert (
        service.open_range(
            ObjectIdentity(BINDING_ID, "serving", "original/id", "v1"),
            start=2,
            end=4,
            expected_length=10,
        ).read()
        == b"abc"
    )
    assert body.closed
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
            "ServerSideEncryption": "AES256",
            **change,
        }
        with pytest.raises(AttachmentError, match="media_unavailable"):
            service.open_range(
                ObjectIdentity(BINDING_ID, "serving", "original/id", "v1"),
                start=2,
                end=4,
                expected_length=10,
            )
        assert body.closed


def test_cleanup_only_exact_keys_all_versions_markers_and_parts():
    from botocore.exceptions import ClientError

    client = private_client()
    upload_id = uuid.uuid4()
    locators = (
        ObjectLocator(BINDING_ID, "quarantine", f"quarantine/{upload_id}"),
        ObjectLocator(BINDING_ID, "serving", f"original/{upload_id}"),
    )
    versions = {locator.key: {"v1", "v2", "marker"} for locator in locators}
    parts = {locator.key for locator in locators}

    def pages(**kwargs):
        key = kwargs["Prefix"]
        current = versions[key]
        if kwargs.get("KeyMarker"):
            return {"IsTruncated": False, "Versions": [{"Key": key, "VersionId": "v2"}]}
        return {
            "IsTruncated": "v2" in current,
            "NextKeyMarker": key,
            "NextVersionIdMarker": "marker",
            "Versions": ([{"Key": key, "VersionId": "v1"}] if "v1" in current else [])
            + [{"Key": key + "-foreign", "VersionId": "foreign"}],
            "DeleteMarkers": [{"Key": key, "VersionId": "marker"}]
            if "marker" in current
            else [],
        }

    def deleted(**kwargs):
        versions[kwargs["Key"]].remove(kwargs["VersionId"])
        return {"VersionId": kwargs["VersionId"]}

    def absent(**kwargs):
        assert kwargs["VersionId"] not in versions[kwargs["Key"]]
        raise ClientError(
            {
                "Error": {"Code": "NoSuchVersion"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )

    def multipart(**kwargs):
        key = kwargs["Prefix"]
        return {
            "IsTruncated": False,
            "Uploads": ([{"Key": key, "UploadId": "part"}] if key in parts else [])
            + [{"Key": key + "-foreign", "UploadId": "foreign"}],
        }

    client.list_object_versions.side_effect = pages
    client.delete_object.side_effect = deleted
    client.head_object.side_effect = absent
    client.list_multipart_uploads.side_effect = multipart
    client.abort_multipart_upload.side_effect = lambda **kwargs: parts.remove(
        kwargs["Key"]
    )
    service = storage(client)
    for locator in locators:
        result = service.erase_all_versions(locator)
        assert result.empty and not result.retryable and result.reason is None
    assert client.delete_object.call_count == 6
    assert client.abort_multipart_upload.call_count == 2
    assert all(
        call.kwargs["Key"].endswith(str(upload_id))
        for call in client.delete_object.call_args_list
    )
    client.list_object_versions.side_effect = RuntimeError("secret-signed-url-CANARY")
    result = service.erase_all_versions(locators[0])
    assert (
        not result.empty and result.retryable and result.reason == "cleanup_unavailable"
    )
    assert "secret-signed-url-CANARY" not in repr(result)


@pytest.mark.django_db(transaction=True)
def test_historical_first_settlement_after_delete(
    historical_attachment_first_settlement,
):
    """Requires schema-owner migration fixture, never a current-row field reset.

    Fixture contract: create pre-value-migration READY verified Blob + Upload,
    no UsageReceipt, retain its settlement Outbox; tombstone through the old
    schema's legal transition, then migrate forward to the selected head.
    Return upload_id and settlement_event_id. The fixture must use an exclusive
    disposable migration database and leave all current fences enabled.
    """
    history = historical_attachment_first_settlement
    upload = AttachmentUpload.objects.select_related("attachment").get(
        pk=history.upload_id
    )
    asset = upload.attachment
    event = AttachmentOutbox.objects.get(pk=history.settlement_event_id)
    assert upload.usage_id is None and upload.usage_created_at is None
    assert asset.state == AttachmentState.DELETED and asset.deleted_at is not None
    assert asset.verified_blob_id is not None and asset.verified_byte_length > 0
    assert event.event == "settled" and event.processed_at is None
    assert event.upload_id == upload.id
    before = (
        asset.verified_blob_id,
        asset.verified_blob_created_at,
        asset.verified_storage_key,
        asset.verified_storage_version,
        asset.verified_sha256,
        asset.verified_byte_length,
        asset.deleted_at,
    )
    first = accounting.consume_settlement(event.id)
    assert first.usage_id is not None and first.usage_created_at is not None
    assert first.usage_outcome == "ready"
    assert first.usage_stored_bytes == before[5]
    assert accounting.consume_settlement(event.id).usage_id == first.usage_id
    assert (
        AttachmentUpload.objects.filter(pk=upload.id, usage_id=first.usage_id).count()
        == 1
    )
    assert (
        AttachmentAuditEvent.objects.filter(
            scope_id=upload.scope_id, entity_id=upload.id, event="settlement"
        ).count()
        == 1
    )
    asset.refresh_from_db()
    assert (
        asset.verified_blob_id,
        asset.verified_blob_created_at,
        asset.verified_storage_key,
        asset.verified_storage_version,
        asset.verified_sha256,
        asset.verified_byte_length,
        asset.deleted_at,
    ) == before
    assert asset.state == AttachmentState.DELETED

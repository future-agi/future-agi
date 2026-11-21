"""PostgreSQL schema tests. NOT runnable without a coordinated disposable DB.

No storage requests. Invented object facts are explicit schema-only fixtures,
not native-provider or migration qualification evidence.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentUpload,
    CaptureSession,
)
from tracer.models.attachment_recording import AttachmentRecordingDerivative
from tracer.models.attachment_storage import (
    AttachmentStorageBinding,
    binding_identity_sha256,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def reservation(now, *, pool, vector, metadata=1):
    names = (
        "charge_bytes",
        "transfers",
        "validation_jobs",
        "recordings",
        "objects",
        "links",
    )
    return {
        "reservation_id": uuid4(),
        "reservation_created_at": now,
        "reservation_expires_at": now + timedelta(hours=1),
        "reservation_pool_key": pool,
        "reservation_reserved_bytes": metadata,
        "reservation_decoded_bytes": 0,
        "reservation_object_count": 6 if pool == "global:derivatives" else 4,
        "reservation_part_count": 1,
        **{
            "reservation_" + key: value
            for key, value in zip(names, vector, strict=True)
        },
    }


@pytest.fixture
def schema_asset(project, user):
    assert connection.vendor == "postgresql", (
        "these tests require the authorized disposable PostgreSQL DB"
    )
    now = timezone.now()
    identity = {
        "provider": "s3",
        "endpoint": "https://storage.invalid",
        "account_identity": "schema-fixture",
        "region": "fixture-region",
        "quarantine_bucket": "fixture-quarantine",
        "serving_bucket": "fixture-serving",
        "quarantine_resource_identity": "fixture:quarantine",
        "serving_resource_identity": "fixture:serving",
        "quarantine_owner_identity": "schema-fixture",
        "serving_owner_identity": "schema-fixture",
        "quarantine_created_at": now,
        "serving_created_at": now,
        "quarantine_encryption_mode": "AES256",
        "serving_encryption_mode": "AES256",
        "quarantine_encryption_key_identity": "",
        "serving_encryption_key_identity": "",
    }
    binding = AttachmentStorageBinding.objects.create(
        **identity, identity_sha256=binding_identity_sha256(identity)
    )
    scope = AttachmentScope.objects.create(
        project=project,
        workspace=project.workspace,
        organization=project.organization,
        enabled=True,
    )
    session = CaptureSession.objects.create(
        scope=scope,
        client_id=uuid4(),
        writer_kind="user",
        writer_id=user.id,
        correlation_nonce=uuid4(),
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        expires_at=now + timedelta(hours=1),
        request_fingerprint="0" * 64,
    )
    asset = Attachment.objects.create(
        scope=scope,
        client_id=uuid4(),
        storage_binding=binding,
        source_kind="Bytes",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
    )
    occurrence = AttachmentOccurrence.objects.create(
        scope=scope,
        attachment=asset,
        capture_session=session,
        client_id=uuid4(),
        trace_id=uuid4(),
        span_id="0123456789abcdef",
        direction="mixed",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        expires_at=session.expires_at,
    )
    uid = uuid4()
    upload = AttachmentUpload.objects.create(
        id=uid,
        scope=scope,
        attachment=asset,
        occurrence=occurrence,
        capture_session=session,
        idempotency_key=str(uuid4()),
        request_fingerprint="1" * 64,
        expected_bytes=1,
        mime_type="audio/wav",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        storage_object_id=uid,
        quarantine_key="quarantine/" + str(uid),
        serving_key="original/" + str(uid),
        expires_at=session.expires_at,
        cleanup_not_before=session.expires_at + timedelta(seconds=15),
        admission_event_id=uuid4(),
        **reservation(
            now, pool="global:" + str(uid.int % 16), vector=(3, 1, 1, 0, 4, 1)
        ),
    )
    AttachmentUpload.objects.filter(pk=uid).update(
        quarantine_write_attempts=1,
        serving_write_attempts=1,
        write_deadline_at=now + timedelta(seconds=30),
    )
    AttachmentUpload.objects.filter(pk=uid).update(
        state="validating",
        received_bytes=1,
        quarantine_version="fixture-q1",
        quarantine_sha256="0" * 64,
        serving_version="fixture-v1",
    )
    Attachment.objects.filter(pk=asset.id).update(state="validating")
    Attachment.objects.filter(pk=asset.id).update(
        state="ready",
        completed_at=now,
        verified_blob_id=uuid4(),
        verified_blob_created_at=now,
        verified_storage_key="original/" + str(uid),
        verified_storage_version="fixture-v1",
        verified_sha256="0" * 64,
        verified_byte_length=1,
        verified_mime_type="audio/wav",
        verified_at=now,
        verified_sample_rate=8000,
        verified_channels=1,
        verified_frame_count=1,
    )
    AttachmentUpload.objects.filter(pk=uid).update(state="ready")
    asset.refresh_from_db()
    upload.refresh_from_db()
    return scope, session, asset, upload


def derivative(schema_asset, *, representation="peaks", object_id=None):
    scope, _, asset, _ = schema_asset
    now = timezone.now()
    pk = uuid4()
    return AttachmentRecordingDerivative.objects.create(
        id=pk,
        scope=scope,
        attachment=asset,
        representation=representation,
        storage_object_id=object_id or pk,
        quarantine_key="quarantine/" + str(pk),
        storage_key="original/" + str(pk),
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        cleanup_not_before=now + timedelta(hours=1, seconds=15),
        available_at=now,
        **reservation(now, pool="global:derivatives", vector=(0, 0, 0, 0, 0, 0)),
    )


def test_raw_sql_cannot_change_verified_asset_or_binding(schema_asset):
    _, _, asset, _ = schema_asset
    for sql, params in [
        (
            "UPDATE tracer_attachment SET verified_sha256=%s WHERE id=%s",
            ["f" * 64, asset.id],
        ),
        (
            "UPDATE tracer_attachment SET storage_binding_id=NULL WHERE id=%s",
            [asset.id],
        ),
        (
            "UPDATE tracer_attachmentstoragebinding SET serving_bucket=%s WHERE id=%s",
            ["changed", asset.storage_binding_id],
        ),
    ]:
        with pytest.raises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql, params)


def test_deleted_verified_media_retains_ready_receipt(schema_asset):
    _, _, asset, upload = schema_asset
    now = timezone.now()
    Attachment.objects.filter(pk=asset.id).update(state="deleted", deleted_at=now)
    AttachmentUpload.objects.filter(pk=upload.id).update(state="deleted")
    receipt_id = uuid4()
    AttachmentUpload.objects.filter(pk=upload.id).update(
        usage_id=receipt_id,
        usage_created_at=now,
        usage_outcome="ready",
        usage_stored_bytes=1,
        settlement_event_id=uuid4(),
    )
    upload.refresh_from_db()
    assert (upload.usage_id, upload.usage_outcome, upload.usage_stored_bytes) == (
        receipt_id,
        "ready",
        1,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentUpload.objects.filter(pk=upload.id).update(
            usage_outcome="deleted", usage_stored_bytes=0
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Attachment.objects.filter(pk=asset.id).update(state="ready", deleted_at=None)


def test_usage_group_and_capacity_release_are_independent(schema_asset):
    _, _, _, upload = schema_asset
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentUpload.objects.filter(pk=upload.id).update(usage_id=uuid4())
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentUpload.objects.filter(pk=upload.id).update(
            reservation_settled_at=timezone.now()
        )


def test_derivative_requires_server_row_identity_and_unique_asset_representation(
    schema_asset,
):
    _, _, asset, _ = schema_asset
    with pytest.raises(IntegrityError), transaction.atomic():
        derivative(schema_asset, object_id=asset.client_id)
    first = derivative(schema_asset)
    assert first.storage_object_id == first.id
    with pytest.raises(IntegrityError), transaction.atomic():
        derivative(schema_asset)


def test_lease_takeover_cannot_reuse_epoch_or_publish_after_tombstone(schema_asset):
    _, _, asset, _ = schema_asset
    row = derivative(schema_asset)
    now = timezone.now()
    AttachmentRecordingDerivative.objects.filter(pk=row.id).update(
        state="recovering",
        lease_epoch=1,
        lease_token=uuid4(),
        lease_until=now + timedelta(minutes=15),
        reservation_charge_bytes=10,
        reservation_transfers=1,
        reservation_validation_jobs=1,
        reservation_objects=6,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentRecordingDerivative.objects.filter(pk=row.id).update(
            lease_token=uuid4()
        )
    AttachmentRecordingDerivative.objects.filter(pk=row.id).update(state="writing")
    AttachmentRecordingDerivative.objects.filter(pk=row.id).update(
        write_attempts=1, write_deadline_at=now + timedelta(seconds=30)
    )
    Attachment.objects.filter(pk=asset.id).update(state="deleted", deleted_at=now)
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentRecordingDerivative.objects.filter(pk=row.id).update(
            state="ready",
            published_at=now,
            storage_version="late-v1",
            sha256="1" * 64,
            byte_length=1,
            mime_type="application/json",
        )
    row.refresh_from_db()
    assert row.state == "writing" and row.storage_version is None


def test_tombstone_blocks_new_occurrences_and_derivatives(schema_asset):
    scope, session, asset, _ = schema_asset
    now = timezone.now()
    Attachment.objects.filter(pk=asset.id).update(state="deleted", deleted_at=now)
    with pytest.raises(IntegrityError), transaction.atomic():
        derivative(schema_asset)
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentOccurrence.objects.create(
            scope=scope,
            attachment=asset,
            capture_session=session,
            client_id=uuid4(),
            trace_id=uuid4(),
            span_id="0123456789abcdef",
            direction="mixed",
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
            expires_at=session.expires_at,
        )


def test_raw_delete_cannot_remove_durable_cleanup_locators(schema_asset):
    _, _, asset, _ = schema_asset
    row = derivative(schema_asset)
    with pytest.raises(IntegrityError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM tracer_attachmentrecordingderivative WHERE id=%s", [row.id]
            )
    assert AttachmentRecordingDerivative.objects.filter(pk=row.id).exists()
    assert Attachment.objects.filter(pk=asset.id).exists()

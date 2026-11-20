"""Real PostgreSQL trigger regressions on the imports lane's disposable DB.

Synthetic object/source facts test database invariants, not native IO or source
HTTP authorization. No trigger, SQL, sandbox, or migration guard is disabled.
"""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from tracer.models.attachment import Attachment, AttachmentScope, CaptureSession
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentRecording,
    AttachmentRecordingSegment,
    AttachmentSourceEnvelope,
)
from tracer.models.attachment_storage import (
    AttachmentStorageBinding,
    binding_identity_sha256,
)
from tracer.services.attachments import capacity, capacity_db, imports, publication
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    ObjectIdentity,
    ResourceVector,
    StoredObject,
    VerifiedAssetFacts,
)

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("ATT_REVIEW_IMPORTS") != "1",
        reason="runs in dedicated backend CI process",
    ),
    pytest.mark.django_db,
    pytest.mark.integration,
]


@pytest.fixture
def import_values(project, user, tmp_path):
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["NAME"].startswith("test_att_review_imports_")
    assert settings.CLICKHOUSE["CH_DATABASE"].startswith("test_att_review_imports_")
    now = timezone.now()
    identity = {
        "provider": "s3",
        "endpoint": "https://storage.invalid",
        "account_identity": "synthetic",
        "region": "fixture",
        "quarantine_bucket": "imports-quarantine",
        "serving_bucket": "imports-serving",
        "quarantine_resource_identity": "fixture:quarantine",
        "serving_resource_identity": "fixture:serving",
        "quarantine_owner_identity": "synthetic",
        "serving_owner_identity": "synthetic",
        "quarantine_created_at": now,
        "serving_created_at": now,
        "quarantine_encryption_mode": "sse-s3",
        "serving_encryption_mode": "sse-s3",
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
        allow_import=True,
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
    trace_id, span = uuid4(), "1234567890abcdef"
    deadline = now + timedelta(minutes=5)
    envelope = AttachmentSourceEnvelope.objects.create(
        scope=scope,
        creator_kind="user",
        creator_id=user.id,
        direction="mixed",
        source_bytes=46,
        key_id="synthetic",
        nonce=b"0" * 12,
        ciphertext=b"synthetic-sql-only",
        expires_at=deadline,
        source_expires_at=deadline,
        generation_kind="http_strong_etag",
        generation='"fixture-v1"',
        mime_type="audio/wav",
        duration_ms=1,
        trace_id=trace_id,
        span_id=span,
    )
    recording = AttachmentRecording.objects.create(
        scope=scope,
        client_id=uuid4(),
        capture_session=session,
        trace_id=trace_id,
        span_id=span,
        capture_session_client_id=session.client_id,
        correlation_nonce=session.correlation_nonce,
        direction="mixed",
        state="importing",
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
    )
    job_id = uuid4()
    amount = ResourceVector(100, 1, 1, 1, 2, 0)
    job = AttachmentImportJob.objects.create(
        id=job_id,
        scope=scope,
        capture_session=session,
        recording=recording,
        storage_binding=binding,
        importer_kind="user",
        importer_id=user.id,
        request_fingerprint="1" * 64,
        source_kind="RemoteUrlEnvelope",
        envelope_id=envelope.id,
        mime_type="audio/wav",
        declared_bytes=46,
        declared_duration_ms=1,
        window_bytes=100,
        max_segment_ms=30000,
        recording_bytes=100000,
        deadline=deadline,
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        scratch_id=job_id,
        cleanup_not_before=now,
        source_generation_kind=envelope.generation_kind,
        source_generation=envelope.generation,
        source_byte_length=46,
        source_mime_type="audio/wav",
        source_duration_ms=1,
        source_expires_at=deadline,
        source_authority_generation=0,
        source_creator_kind="user",
        source_creator_id=user.id,
        source_trace_id=trace_id,
        source_span_id=span,
        source_direction="mixed",
        **imports._reservation(now, deadline, amount, source_bytes=46, window=100),
    )
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=scope.id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        capacity_db.reserve_locked(
            locks, owner=imports._owner(job), amount=amount, expires_at=deadline
        )
    job.refresh_from_db()
    return scope, session, binding, job, tmp_path


@pytest.mark.parametrize("capacity_failure", [False, True])
def test_expired_takeover_lease_precedes_guarded_resize(
    import_values, settings, capacity_failure
):
    scope, session, binding, job, directory = import_values
    settings.FI_ATTACHMENTS_WORK_DIR = str(directory)
    if capacity_failure:
        with transaction.atomic():
            locks = capacity_db.lock_capacity(
                scope_id=scope.id, pool_keys=(capacity.IMPORT_POOL_KEY,)
            )
            capacity_db.resize_locked(
                locks,
                owner=imports._owner(job),
                amount=ResourceVector(capacity.PROJECT_CAP.bytes, 1, 1, 1, 2, 0),
            )
    job.refresh_from_db()
    job.lease_epoch, job.attempts, job.lease_phase = 1, 1, "recovering"
    job.lease_token = uuid4()
    job.lease_until = timezone.now() + timedelta(seconds=0.5)
    job.save(
        update_fields=[
            "lease_epoch",
            "attempts",
            "lease_phase",
            "lease_token",
            "lease_until",
        ]
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(0.6)")
    old = (job.lease_epoch, job.lease_token, job.lease_until, job.attempts)
    old_vector = capacity_db.owner_vector(job)
    # Demonstrate the concrete old failure with the actual retained trigger.
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentImportJob.objects.filter(pk=job.id).update(
            reservation_charge_bytes=job.reservation_charge_bytes
        )

    def takeover():
        with transaction.atomic():
            locks = capacity_db.lock_capacity(
                scope_id=scope.id, pool_keys=(capacity.IMPORT_POOL_KEY,)
            )
            AttachmentScope.objects.select_for_update().get(pk=scope.id)
            CaptureSession.objects.select_for_update().get(pk=session.id)
            AttachmentSourceEnvelope.objects.select_for_update().get(pk=job.envelope_id)
            locked = AttachmentImportJob.objects.select_for_update().get(pk=job.id)
            return imports._claim_locked(locked, locks, timezone.now())

    if capacity_failure:
        with pytest.raises(AttachmentError) as error:
            takeover()
        assert error.value.code == "too_large"
        job.refresh_from_db()
        assert (job.lease_epoch, job.lease_token, job.lease_until, job.attempts) == old
        assert capacity_db.owner_vector(job) == old_vector
        assert not (directory / "import" / str(job.id) / "2").exists()
    else:
        fence = takeover()
        job.refresh_from_db()
        assert fence.epoch == 2 and job.lease_phase == "recovering"
        assert job.lease_until > timezone.now() and job.lease_token != old[1]
        assert capacity_db.owner_vector(job) == old_vector + ResourceVector(
            146, 1, 1, 0, 2, 0
        )
        assert (directory / "import" / str(job.id) / "2" / "guard").exists()


def test_pending_validating_ready_whole_asset_keeps_initial_sql_fence(
    import_values, settings
):
    scope, session, binding, job, directory = import_values
    settings.FI_ATTACHMENTS_WORK_DIR = str(directory)
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=scope.id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        job = AttachmentImportJob.objects.select_for_update().get(pk=job.id)
        fence = imports._claim_locked(job, locks, timezone.now())
        job.lease_phase = "writing"
        job.save(update_fields=["lease_phase"])
    fields = {
        "scope": scope,
        "client_id": uuid4(),
        "storage_binding": binding,
        "source_kind": "imported_recording",
        "policy_version": scope.policy_version,
        "deletion_generation": scope.deletion_generation,
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        Attachment.objects.create(**fields, state="validating")
    sid = uuid4()
    segment = AttachmentRecordingSegment.objects.create(
        id=sid,
        scope=scope,
        recording_id=job.recording_id,
        import_job=job,
        storage_binding=binding,
        storage_object_id=sid,
        storage_key=f"original/{sid}",
        quarantine_key=f"quarantine/{sid}",
        index=0,
        attachment_client_id=fields["client_id"],
        occurrence_client_id=uuid4(),
        start_frame=0,
        frame_count=1,
        start_ms=0,
        duration_ms=1,
        byte_length=46,
        sha256="a" * 64,
        allocation_epoch=fence.epoch,
        cleanup_not_before=job.deadline + timedelta(seconds=15),
    )
    segment.write_attempts = 1
    segment.write_deadline_at = timezone.now() + timedelta(seconds=30)
    segment.save(update_fields=["write_attempts", "write_deadline_at"])
    segment.storage_version = "recorded-fixture-v1"
    segment.save(update_fields=["storage_version"])
    with transaction.atomic():
        AttachmentScope.objects.select_for_update().get(pk=scope.id)
        AttachmentImportJob.objects.select_for_update().get(pk=job.id)
        segment = AttachmentRecordingSegment.objects.select_for_update().get(pk=sid)
        asset = Attachment.objects.create(**fields, state="pending_upload")
        asset.state = "validating"
        asset.save(update_fields=["state"])
        segment.attachment = asset
        segment.save(update_fields=["attachment"])
        facts = VerifiedAssetFacts(
            StoredObject(
                ObjectIdentity(
                    binding.id, "serving", segment.storage_key, segment.storage_version
                ),
                46,
                "a" * 64,
            ),
            "audio/wav",
            timezone.now(),
            8000,
            1,
            1,
        )
        publication.publish_verified_locked(
            attachment=asset,
            facts=facts,
            expected_binding_id=binding.id,
            scope=scope,
            fence=fence,
            allocation=segment,
        )
        segment.state = "published"
        segment.save(update_fields=["state"])
    asset.refresh_from_db()
    assert (
        asset.state == "ready"
        and asset.verified_storage_version == "recorded-fixture-v1"
    )

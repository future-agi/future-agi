"""Component acceptance; storage doubles do not qualify a deployment backend."""

import hashlib
import io
import threading
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone

from accounts.models import OrgApiKey
from accounts.models.workspace import Workspace
from tracer.models.attachment import (
    AttachmentBlob,
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    AttachmentWriteGrant,
    CaptureSession,
    UploadReservation,
)
from tracer.models.project import Project
from tracer.services.attachments import bootstrap as broker
from tracer.services.attachments import uploads, validation
from tracer.services.attachments.contract import (
    AttachmentError,
    CapturePolicy,
    admit_audio,
    span_id,
    trace_uuid,
)
from tracer.services.attachments.probe import probe
from tracer.services.attachments.storage import ObjectIdentity

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x01\x00\x02" * 240)
    return stream.getvalue()


class ImmutableStore:
    def __init__(self):
        self.objects = {}
        self.promotions = []

    def put_quarantine(self, upload_id, stream, length):
        key = f"quarantine/{upload_id}"
        if key in self.objects:
            raise AttachmentError("already_exists", 409)
        self.objects[key] = stream.read()
        assert len(self.objects[key]) == length
        return ObjectIdentity(key, "version-1")

    def snapshot(self, identity, output, *, expected_length):
        assert identity.version == "version-1"
        data = self.objects[identity.key]
        assert len(data) == expected_length
        output.write(data)
        return hashlib.sha256(data).hexdigest()

    def promote(self, snapshot, *, upload_id, expected_length, expected_sha256):
        snapshot.seek(0)
        data = snapshot.read()
        assert len(data) == expected_length
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        self.promotions.append(data)
        return ObjectIdentity(f"original/{uuid.uuid4()}", "immutable-v1")


@pytest.fixture
def setup(project, user, settings):
    settings.FI_ATTACHMENTS_ENABLED = True
    scope = AttachmentScope.objects.create(
        project=project,
        workspace=project.workspace,
        organization=project.organization,
        enabled=True,
        require_media_redaction=False,
    )
    writer = broker.Writer("user", user.pk, scope.organization_id, scope.workspace_id)
    return scope, writer


TRACE = "00112233445566778899aabbccddeeff"
SPAN = "0123456789abcdef"


def session(setup, **overrides):
    """Bootstrap the writer's capture session against the scope's project."""
    scope, writer = setup
    body = {
        "schema_versions": [1],
        "project_name": scope.project.name,
        "project_type": scope.project.trace_type,
        "capture_session_id": str(uuid.uuid4()),
        "correlation_nonce": str(uuid.uuid4()),
    }
    body.update(overrides)
    return broker.bootstrap(writer, body)


def upload_body(scope, current, **overrides):
    body = {
        "schema_version": 1,
        "attachment_id": str(uuid.uuid4()),
        "occurrence_id": str(uuid.uuid4()),
        "association": {
            "capture_session_id": str(current.client_id),
            "correlation_nonce": str(current.correlation_nonce),
            "parent": {"trace_id": TRACE, "span_id": SPAN},
        },
        "policy_version": str(scope.policy_version),
        "deletion_generation": scope.deletion_generation,
        "mime_type": "audio/wav",
        "modality": "audio",
        "direction": "mixed",
        "source_kind": "Bytes",
        "declared_bytes": len(wav_bytes()),
    }
    body.update(overrides)
    return body


def initialized(setup, *, current=None, key=None, **overrides):
    """Returns (upload, write capability, writer, {"body", "key", "session",
    "expires_at"}); ``expires_at`` is the capability's, not the upload's."""
    scope, writer = setup
    scope.refresh_from_db()
    if current is None:
        current, _ = session(setup)
    body = upload_body(scope, current, **overrides)
    key = key or f"up-{uuid.UUID(body['occurrence_id']).hex}"
    upload, token, expires_at = uploads.initialize(
        writer, scope_id=scope.id, body=body, key=key
    )
    return (
        upload,
        token,
        writer,
        {"body": body, "key": key, "session": current, "expires_at": expires_at},
    )


@pytest.mark.parametrize(
    "raw",
    [
        "0" * 32,
        "00" * 16,
        "f" * 31,
        "G" * 32,
        "A" * 32,
        None,
        "urn:uuid:00112233-4455-6677-8899-aabbccddeeff",
    ],
)
def test_bad_trace_ids(raw):
    with pytest.raises(AttachmentError, match="invalid_trace_id"):
        trace_uuid(raw)


def test_trace_identity_preserves_bits():
    assert (
        str(trace_uuid("00112233445566778899aabbccddeeff"))
        == "00112233-4455-6677-8899-aabbccddeeff"
    )
    assert trace_uuid("00112233445566778899aabbccddeeff") == trace_uuid(
        "00112233-4455-6677-8899-aabbccddeeff"
    )


@pytest.mark.parametrize("raw", ["0" * 16, "a" * 15, "A" * 16, None])
def test_bad_span_ids(raw):
    with pytest.raises(AttachmentError, match="invalid_span_id"):
        span_id(raw)


@pytest.mark.parametrize(
    "change,direction,reason",
    [
        ({"enabled": False}, "input", "disabled"),
        ({"capture_content": False}, "output", "policy_denied"),
        ({"hide_inputs": True}, "input", "policy_denied"),
        ({"hide_inputs": True}, "mixed", "policy_denied"),
        ({"hide_outputs": True}, "mixed", "policy_denied"),
        ({"hide_input_text": True}, "mixed", "policy_denied"),
        ({"hide_output_text": True}, "output", "policy_denied"),
        ({"pii_redaction": True}, "output", "redaction_unavailable"),
        ({"enabled": "true"}, "input", "invalid_policy"),
    ],
)
def test_deny_wins_before_source_access(change, direction, reason):
    scope = SimpleNamespace(
        enabled=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=False,
    )
    with pytest.raises(AttachmentError, match=reason):
        admit_audio(
            scope,
            replace(CapturePolicy(enabled=True), **change),
            direction=direction,
            source_kind="Bytes",
        )


def test_mixed_denied_but_independent_output_allowed():
    scope = SimpleNamespace(
        enabled=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=False,
    )
    policy = CapturePolicy(enabled=True, hide_inputs=True)
    admit_audio(scope, policy, direction="output", source_kind="Bytes")
    with pytest.raises(AttachmentError):
        admit_audio(scope, policy, direction="mixed", source_kind="Bytes")


def test_pii_requires_separate_server_and_client_choices():
    scope = SimpleNamespace(
        enabled=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=True,
    )
    with pytest.raises(AttachmentError, match="redaction_unavailable"):
        admit_audio(
            scope,
            CapturePolicy(enabled=True, pii_redaction=True),
            direction="input",
            source_kind="Bytes",
        )
    policy = CapturePolicy(
        enabled=True, pii_redaction=True, allow_unredacted_media_with_text_pii=True
    )
    admit_audio(scope, policy, direction="input", source_kind="Bytes")
    scope.require_media_redaction = True
    with pytest.raises(AttachmentError, match="redaction_unavailable"):
        admit_audio(scope, policy, direction="input", source_kind="Bytes")


def test_recording_separate_from_import():
    scope = SimpleNamespace(
        enabled=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=False,
    )
    policy = CapturePolicy(enabled=True)
    admit_audio(scope, policy, direction="mixed", source_kind="OwnedRecordingFile")
    with pytest.raises(AttachmentError, match="recording_disabled"):
        admit_audio(
            scope,
            policy,
            direction="mixed",
            source_kind="Bytes",
            creates_recording=True,
        )


def test_default_off_creates_nothing(setup, settings):
    settings.FI_ATTACHMENTS_ENABLED = False
    with pytest.raises(AttachmentError, match="disabled"):
        session(setup)
    assert not broker.CaptureSession.objects.exists()


def test_session_renewal_and_exact_binding(setup, user):
    sid, nonce = str(uuid.uuid4()), str(uuid.uuid4())
    first, document = session(setup, capture_session_id=sid, correlation_nonce=nonce)
    second, renewed = session(setup, capture_session_id=sid, correlation_nonce=nonce)
    # BR-2: the same writer + pair renews the same session with a later expiry.
    assert first.id == second.id
    assert renewed["expires_at"] >= document["expires_at"]
    assert renewed["scope"] == document["scope"]
    assert document["policy"]["version"] == "1"
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        session(setup, capture_session_id=sid, correlation_nonce=str(uuid.uuid4()))
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        session(setup, correlation_nonce=nonce)
    scope, writer = setup
    other = OrgApiKey.no_workspace_objects.create(
        type="system",
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    stranger = broker.Writer(
        "api_key", other.id, scope.organization_id, scope.workspace_id
    )
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        session((scope, stranger), capture_session_id=sid, correlation_nonce=nonce)


@pytest.mark.parametrize(
    "operation",
    [
        "list",
        "resolve",
        "download",
        "delete",
        "relink",
        "imports",
        "provider_connection",
    ],
)
def test_capabilities_cannot_acquire_read_or_other_authority(setup, operation):
    upload, token, _, _ = initialized(setup)
    with pytest.raises(AttachmentError, match="not_found"):
        broker.authorize(
            token, operation=operation, scope_id=upload.scope_id, upload_id=upload.id
        )


def test_exact_upload_and_scope_binding(setup):
    upload, token, _, _ = initialized(setup)
    for scope_id, upload_id in [
        (uuid.uuid4(), upload.id),
        (upload.scope_id, uuid.uuid4()),
    ]:
        with pytest.raises(AttachmentError, match="not_found"):
            broker.authorize(
                token, operation="status", scope_id=scope_id, upload_id=upload_id
            )
    assert uploads.status(token, scope_id=upload.scope_id, upload_id=upload.id) == {
        "upload_id": str(upload.id),
        "state": "pending_upload",
        "reason": "queued",
    }


def test_restricted_key_needs_live_explicit_eligibility(setup, user):
    scope, _ = setup
    key = OrgApiKey.no_workspace_objects.create(
        type="ingest",
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        simulation_agent_id=uuid.uuid4(),
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    writer = broker.Writer("api_key", key.id, scope.organization_id, scope.workspace_id)
    with pytest.raises(AttachmentError, match="credential_scope_unsupported"):
        session((scope, writer))
    grant = AttachmentWriteGrant.objects.create(
        scope=scope,
        api_key=key,
        granted_by=user,
        enabled=True,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    upload, token, _, args = initialized((scope, writer))
    broker.authorize(token, operation="status", scope_id=scope.id, upload_id=upload.id)
    grant.enabled = False
    grant.save(update_fields=["enabled"])
    with pytest.raises(AttachmentError, match="credential_scope_unsupported"):
        broker.authorize(
            token, operation="status", scope_id=scope.id, upload_id=upload.id
        )
    assert args["session"].writer_id == key.id


def test_policy_change_revokes_before_transfer(setup):
    scope, _ = setup
    upload, token, _, _ = initialized(setup)
    AttachmentScope.objects.filter(pk=scope.id).update(policy_version=2)
    consumed = Mock(side_effect=AssertionError("source must not be read"))
    with pytest.raises(AttachmentError, match="capability_expired"):
        uploads.transfer(
            token,
            scope_id=scope.id,
            upload_id=upload.id,
            chunks=consumed,
            storage=ImmutableStore(),
        )
    consumed.assert_not_called()


def test_workspace_and_type_identity(setup, user):
    scope, writer = setup
    other_ws = Workspace.objects.create(
        name="isolated", organization_id=scope.organization_id, created_by=user
    )
    for workspace, kind in [(scope.workspace, "observe"), (other_ws, "experiment")]:
        Project.no_workspace_objects.create(
            name=scope.project.name,
            trace_type=kind,
            organization_id=scope.organization_id,
            workspace=workspace,
            model_type=scope.project.model_type,
        )
    assert (
        broker.resolve_scope(
            writer, project_name=scope.project.name, project_type="experiment"
        ).id
        == scope.id
    )
    assert (
        broker.resolve_scope(
            writer, project_name=scope.project.name, project_type="observe"
        ).id
        != scope.id
    )
    # A missing project is created exactly as ingestion would, with a scope
    # that stays disabled until an owner enables it.
    created = broker.resolve_scope(
        writer, project_name="missing", project_type="experiment"
    )
    assert created.id != scope.id and created.enabled is False
    for bad_type in ("unknown", "", "Observe"):
        with pytest.raises(AttachmentError, match="scope_unresolved"):
            broker.resolve_scope(writer, project_name="another", project_type=bad_type)
    with pytest.raises(AttachmentError, match="scope_unresolved"):
        broker.resolve_scope(
            replace(writer, workspace_id=uuid.uuid4()),
            project_name=scope.project.name,
            project_type="experiment",
        )


def test_duplicate_client_ids_scoped(setup, user):
    first, token, _, args = initialized(setup)
    scope, writer = setup
    other_project = Project.no_workspace_objects.create(
        name="second",
        trace_type="experiment",
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        model_type=scope.project.model_type,
    )
    other = AttachmentScope.objects.create(
        project=other_project,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        enabled=True,
        require_media_redaction=False,
    )
    body = args["body"]
    second, _, _, _ = initialized(
        (other, writer),
        attachment_id=body["attachment_id"],
        occurrence_id=body["occurrence_id"],
    )
    assert first.id != second.id
    assert first.occurrence.client_id == second.occurrence.client_id
    with pytest.raises(AttachmentError, match="not_found"):
        uploads.status(token, scope_id=other.id, upload_id=second.id)


def test_upload_idempotency_and_conflict(setup):
    scope, writer = setup
    first, token, _, args = initialized(setup)
    # BR-1: an identical replay renews the capability for the same job.
    second, renewed, expires_at = uploads.initialize(
        writer, scope_id=scope.id, body=args["body"], key=args["key"]
    )
    assert first.id == second.id and renewed != token
    assert expires_at >= args["expires_at"]
    assert UploadReservation.objects.count() == 1
    broker.authorize(renewed, operation="status", scope_id=scope.id, upload_id=first.id)
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        uploads.initialize(
            writer,
            scope_id=scope.id,
            body={**args["body"], "declared_bytes": args["body"]["declared_bytes"] + 1},
            key=args["key"],
        )
    # A different key cannot claim the same occurrence.
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        uploads.initialize(writer, scope_id=scope.id, body=args["body"], key="up-other")


def test_upload_lifetime_is_separate_from_its_capability(setup):
    """BR-1 after expiry needs a job that outlives its 300 s capability."""
    upload, _, _, args = initialized(setup)
    reservation = UploadReservation.objects.get(upload=upload)
    capability_expiry = args["expires_at"]
    assert upload.expires_at == reservation.expires_at
    assert capability_expiry < upload.expires_at <= upload.occurrence.expires_at
    now = timezone.now()
    capability_left = (capability_expiry - now).total_seconds()
    upload_left = (upload.expires_at - now).total_seconds()
    assert abs(capability_left - broker.CAPABILITY_SECONDS) < 5
    assert abs(upload_left - uploads.UPLOAD_SECONDS) < 5
    # Renewal mints a capability but never moves the job or its reservation.
    scope, writer = setup
    _, _, renewed = uploads.initialize(
        writer, scope_id=scope.id, body=args["body"], key=args["key"]
    )
    reservation.refresh_from_db()
    upload.refresh_from_db()
    assert renewed < upload.expires_at == reservation.expires_at


@pytest.mark.parametrize(
    "chunks,code",
    [
        ([b"a" * (uploads.CHUNK_BYTES + 1)], "invalid_chunk"),
        ([wav_bytes() + b"x"], "too_large"),
        ([b"short"], "length_mismatch"),
    ],
)
def test_malformed_transfer_is_discarded_and_replayable(setup, chunks, code):
    upload, token, _, _ = initialized(setup)
    storage = ImmutableStore()
    with pytest.raises(AttachmentError, match=code):
        uploads.transfer(
            token,
            scope_id=upload.scope_id,
            upload_id=upload.id,
            chunks=chunks,
            storage=storage,
        )
    assert storage.objects == {}
    upload.refresh_from_db()
    # BR-3: the partial transfer is discarded and the lease released.
    assert upload.state == "pending_upload" and upload.transfer_started_at is None
    assert uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=storage,
    ) == {"upload_id": str(upload.id), "state": "pending_upload", "reason": "queued"}
    assert len(storage.objects) == 1


def test_abandoned_reservation_is_released_once(setup):
    upload, token, _, _ = initialized(setup)
    uploads.abort(token, scope_id=upload.scope_id, upload_id=upload.id)
    assert not uploads.settle(
        upload.id, scope_id=upload.scope_id, outcome=AttachmentState.FAILED
    )
    assert all(
        row.reserved_bytes == row.active_transfers == row.validation_jobs == 0
        for row in AttachmentBudget.objects.all()
    )


def test_complete_is_validation_not_readiness(setup):
    upload, token, _, _ = initialized(setup)
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=ImmutableStore(),
    )
    first = uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)
    second = uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)
    assert first.state == second.state == "validating"
    assert AttachmentOutbox.objects.filter(event="validate").count() == 1
    assert not AttachmentBlob.objects.exists()
    assert AttachmentOccurrence.objects.get(pk=upload.occurrence_id).bound_at is None


def test_replay_and_delete_do_not_resurrect(setup):
    upload, token, _, _ = initialized(setup)
    storage = ImmutableStore()
    args = {"scope_id": upload.scope_id, "upload_id": upload.id, "storage": storage}
    uploads.transfer(token, chunks=[wav_bytes()], **args)
    # BR-3: a byte-identical replay succeeds without a second object.
    assert uploads.transfer(token, chunks=[wav_bytes()], **args)["state"] == (
        "pending_upload"
    )
    assert len(storage.objects) == 1
    altered = bytearray(wav_bytes())
    altered[-1] ^= 1
    with pytest.raises(AttachmentError, match="transfer_conflict"):
        uploads.transfer(token, chunks=[bytes(altered)], **args)
    uploads.abort(token, scope_id=upload.scope_id, upload_id=upload.id)
    with pytest.raises(AttachmentError, match="incomplete_upload"):
        uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)
    with pytest.raises(AttachmentError, match="transfer_conflict"):
        uploads.transfer(token, chunks=[wav_bytes()], **args)
    assert not AttachmentBlob.objects.exists()


@pytest.mark.parametrize(
    "index,field,maximum",
    [
        (0, "active_transfers", 128 // uploads.GLOBAL_SHARDS),
        (0, "initialization_attempts", 960 // uploads.GLOBAL_SHARDS),
        (1, "reserved_bytes", 2 * 1024**3),
        (2, "active_transfers", 16),
        (2, "validation_jobs", 1024),
        (2, "initialization_attempts", 120),
    ],
)
def test_finite_aggregate_admission(setup, index, field, maximum):
    scope, _ = setup
    # The deployment budget (index 0) is sharded and the upload's shard comes
    # from an id allocated inside initialize, so every shard is filled.
    shards = range(uploads.GLOBAL_SHARDS) if index == 0 else [0]
    for key in {uploads._budget_keys(scope, shard)[index] for shard in shards}:
        AttachmentBudget.objects.create(
            key=key, window_started_at=timezone.now(), **{field: maximum}
        )
    with pytest.raises(AttachmentError, match="throttled"):
        initialized(setup)
    assert not AttachmentUpload.objects.exists()


def test_validated_bytes_are_immutable_snapshot_and_unbound(setup, monkeypatch):
    upload, token, _, _ = initialized(setup)
    storage = ImmutableStore()
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=storage,
    )
    uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)

    def probing(path):
        # Simulate mutable-latest overwrite after snapshot: never promoted.
        storage.objects[f"quarantine/{upload.id}"] = b"attacker"
        return probe(path)

    monkeypatch.setattr(validation, "sandbox_probe", probing)
    blob = validation.validate(upload.id, scope_id=upload.scope_id, storage=storage)
    assert storage.promotions == [wav_bytes()]
    assert blob.storage_version == "immutable-v1"
    assert blob.sha256 == hashlib.sha256(wav_bytes()).hexdigest()
    assert AttachmentOccurrence.objects.get(pk=upload.occurrence_id).bound_at is None
    assert not uploads.settle(
        upload.id, scope_id=upload.scope_id, outcome=AttachmentState.REJECTED
    )


def test_policy_or_delete_during_probe_fences_promotion(setup, monkeypatch):
    upload, token, _, _ = initialized(setup)
    storage = ImmutableStore()
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=storage,
    )
    uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)

    def probing(path):
        AttachmentScope.objects.filter(pk=upload.scope_id).update(deletion_generation=1)
        return probe(path)

    monkeypatch.setattr(validation, "sandbox_probe", probing)
    with pytest.raises(AttachmentError, match="capability_expired"):
        validation.validate(upload.id, scope_id=upload.scope_id, storage=storage)
    assert storage.promotions == [] and not AttachmentBlob.objects.exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>active</html>",
        b"#EXTM3U\nhttps://example.test/private",
        b"RIFF\xff\xff\xff\xffWAVE",
        wav_bytes()[:-1],
        wav_bytes() + b"metadata",
    ],
)
def test_probe_rejects_spoof_corruption_external_resources(tmp_path, payload):
    path = tmp_path / "input"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        probe(path)


def test_probe_pcm_facts(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(wav_bytes())
    assert probe(path) == {
        "mime_type": "audio/wav",
        "channels": 2,
        "sample_rate": 24000,
        "frame_count": 240,
    }


def test_sandbox_unavailable_has_no_in_process_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        validation.subprocess,
        "run",
        Mock(side_effect=FileNotFoundError("secret-canary")),
    )
    with pytest.raises(AttachmentError, match="validation_unavailable") as exc:
        validation.sandbox_probe(tmp_path / "input")
    assert "secret-canary" not in str(exc.value)


def test_database_tombstone_cannot_be_reset(setup):
    upload, token, _, _ = initialized(setup)
    uploads.abort(token, scope_id=upload.scope_id, upload_id=upload.id)
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentUpload.objects.filter(pk=upload.id).update(state="pending_upload")


def test_database_cross_scope_link_rejected(setup):
    upload, _, _, _ = initialized(setup)
    with pytest.raises(IntegrityError), transaction.atomic():
        AttachmentOccurrence.objects.filter(pk=upload.occurrence_id).update(
            trace_id=uuid.uuid4()
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_and_settlement(setup):
    upload, token, _, _ = initialized(setup)
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=ImmutableStore(),
    )

    def finish(_):
        close_old_connections()
        try:
            return uploads.complete(
                token, scope_id=upload.scope_id, upload_id=upload.id
            ).state
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(finish, range(2))) == ["validating", "validating"]
    assert AttachmentOutbox.objects.filter(event="validate").count() == 1


# --- #3945 hardening (decision 7 load-test findings) -------------------------


@pytest.mark.parametrize("replay", [False, True], ids=["fresh", "replay"])
def test_revocation_after_session_lookup_admits_nothing(setup, monkeypatch, replay):
    """A revocation committed after ``find_session`` read the session, but
    before the locked admission, refuses it: no occurrence, upload or
    reservation is created and no capability is re-issued. The load test
    measured about one late occurrence per in-flight writer before the fix."""
    scope, writer = setup
    current, _ = session(setup)
    body = upload_body(scope, current)
    key = f"up-{uuid.UUID(body['occurrence_id']).hex}"
    if replay:
        uploads.initialize(writer, scope_id=scope.id, body=body, key=key)

    def counts():
        return (
            AttachmentOccurrence.objects.count(),
            AttachmentUpload.objects.count(),
            UploadReservation.objects.count(),
            AttachmentOutbox.objects.filter(event="admitted").count(),
        )

    before = counts()
    real = uploads.find_session

    def lookup_then_revoke(*args):
        found = real(*args)
        CaptureSession.objects.filter(pk=found.pk).update(revoked_at=timezone.now())
        return found

    monkeypatch.setattr(uploads, "find_session", lookup_then_revoke)
    with pytest.raises(AttachmentError, match="capability_expired"):
        uploads.initialize(writer, scope_id=scope.id, body=body, key=key)
    assert counts() == before


@pytest.mark.django_db(transaction=True)
def test_session_renewal_does_not_queue_behind_fk_key_share(setup):
    """Renewal locks the scope and session rows FOR NO KEY UPDATE, so an open
    transaction holding the FK KEY SHARE that every attachment insert takes on
    those rows does not block it. With FOR UPDATE, renewals and inserts queued
    behind each other (most raw-shape lock waits in the load test)."""
    scope, _ = setup
    sid, nonce = str(uuid.uuid4()), str(uuid.uuid4())
    current, _ = session(setup, capture_session_id=sid, correlation_nonce=nonce)
    holding, release = threading.Event(), threading.Event()

    def hold():
        close_old_connections()
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM tracer_attachmentscope WHERE id = %s FOR KEY SHARE",
                    [scope.id],
                )
                cursor.execute(
                    "SELECT 1 FROM tracer_capturesession WHERE id = %s FOR KEY SHARE",
                    [current.id],
                )
                holding.set()
                release.wait(15)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold)
        assert holding.wait(15)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '2s'")
            renewed, _ = session(setup, capture_session_id=sid, correlation_nonce=nonce)
        finally:
            release.set()
            with connection.cursor() as cursor:
                cursor.execute("RESET lock_timeout")
        holder.result(timeout=15)
    assert renewed.id == current.id


def test_reservation_sweep_uses_the_unsettled_partial_index(setup):
    """``expire_reservations`` reads unsettled rows in expiry order from the
    partial index; the old (settled_at, expires_at) index seq-scanned 100k."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s",
            ["attachment_reservation_due_idx"],
        )
        (definition,) = cursor.fetchone()
        cursor.execute("SET LOCAL enable_seqscan = off")
        plan = (
            UploadReservation.objects.filter(
                settled_at__isnull=True, expires_at__lte=timezone.now()
            )
            .order_by("expires_at")[:100]
            .explain()
        )
    assert "(expires_at) WHERE (settled_at IS NULL)" in definition
    assert "attachment_reservation_due_idx" in plan


def _upload_ids(monkeypatch, *shards):
    """Make initialize allocate upload ids that land on the given shards."""
    ids = iter(
        uuid.UUID(int=index * uploads.GLOBAL_SHARDS + shard)
        for index, shard in enumerate(shards, start=1)
    )
    monkeypatch.setattr(uploads, "uuid4", lambda: next(ids))


def test_admission_and_settle_use_the_same_global_shard(setup, monkeypatch):
    """An upload is charged to one ``global:<shard>`` row named by its id, and
    settling it releases that same row. No single 'global' row exists."""
    _upload_ids(monkeypatch, 5)
    upload, token, _, _ = initialized(setup)
    assert uploads.global_shard(upload.id) == 5
    charged = {
        row.key: (row.active_transfers, row.validation_jobs, row.reserved_bytes)
        for row in AttachmentBudget.objects.filter(key__startswith="global")
    }
    assert charged == {"global:5": (1, 1, upload.expected_bytes)}
    uploads.abort(token, scope_id=upload.scope_id, upload_id=upload.id)
    row = AttachmentBudget.objects.get(key="global:5")
    assert (row.active_transfers, row.validation_jobs, row.reserved_bytes) == (0, 0, 0)
    assert AttachmentBudget.objects.filter(key__startswith="global").count() == 1


def test_a_full_global_shard_refuses_only_its_own_uploads(setup, monkeypatch):
    """Each shard holds 1/GLOBAL_SHARDS of the deployment caps."""
    scope, _ = setup
    cap = uploads.BUDGETS[0][0] // uploads.GLOBAL_SHARDS
    AttachmentBudget.objects.create(
        key="global:3", window_started_at=timezone.now(), active_transfers=cap
    )
    _upload_ids(monkeypatch, 3, 4)
    with pytest.raises(AttachmentError, match="throttled"):
        initialized(setup)
    upload, _, _, _ = initialized(setup)
    assert uploads.global_shard(upload.id) == 4
    assert AttachmentBudget.objects.get(key="global:3").active_transfers == cap


def test_project_cap_stays_exact_across_global_shards(setup, monkeypatch):
    """Per-scope budgets stay authoritative: a project admits exactly its own
    cap of transfers spread over every shard (each shard's cap is half the
    project's), and the next one is refused by the project row, not a shard."""
    scope, _ = setup
    project_cap = uploads.BUDGETS[2][0]
    assert project_cap > uploads.BUDGETS[0][0] // uploads.GLOBAL_SHARDS
    shards = [index % uploads.GLOBAL_SHARDS for index in range(project_cap + 1)]
    _upload_ids(monkeypatch, *shards)
    current, _ = session(setup)
    for _ in range(project_cap):
        initialized(setup, current=current)
    with pytest.raises(AttachmentError, match="throttled"):
        initialized(setup, current=current)
    project = AttachmentBudget.objects.get(key=f"project:{scope.project_id}")
    assert project.active_transfers == project_cap
    by_shard = dict(
        AttachmentBudget.objects.filter(key__startswith="global:").values_list(
            "key", "active_transfers"
        )
    )
    assert sum(by_shard.values()) == project_cap
    assert max(by_shard.values()) < uploads.BUDGETS[0][0] // uploads.GLOBAL_SHARDS

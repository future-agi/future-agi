"""Component acceptance; storage doubles do not qualify a deployment backend."""

import hashlib
import io
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import IntegrityError, close_old_connections, transaction
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


def session(setup, **overrides):
    scope, writer = setup
    values = {
        "scope_id": scope.id,
        "client_id": uuid.uuid4(),
        "trace_id": "00112233445566778899aabbccddeeff",
        "parent_span_id": "0123456789abcdef",
        "policy": CapturePolicy(enabled=True),
    }
    values.update(overrides)
    return broker.bootstrap(writer, **values)


def initialized(setup, **overrides):
    scope, _ = setup
    current, token = session(setup)
    args = {
        "scope_id": scope.id,
        "upload_id": uuid.uuid4(),
        "attachment_id": uuid.uuid4(),
        "occurrence_id": uuid.uuid4(),
        "expected_bytes": len(wav_bytes()),
        "mime_type": "audio/wav",
        "direction": "mixed",
        "source_kind": "bytes",
    }
    args.update(overrides)
    upload, transfer_token = uploads.initialize(token, **args)
    return upload, transfer_token, token, args


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
            source_kind="bytes",
        )


def test_mixed_denied_but_independent_output_allowed():
    scope = SimpleNamespace(
        enabled=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=False,
    )
    policy = CapturePolicy(enabled=True, hide_inputs=True)
    admit_audio(scope, policy, direction="output", source_kind="bytes")
    with pytest.raises(AttachmentError):
        admit_audio(scope, policy, direction="mixed", source_kind="bytes")


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
            source_kind="bytes",
        )
    policy = CapturePolicy(
        enabled=True, pii_redaction=True, allow_unredacted_media_with_text_pii=True
    )
    admit_audio(scope, policy, direction="input", source_kind="bytes")
    scope.require_media_redaction = True
    with pytest.raises(AttachmentError, match="redaction_unavailable"):
        admit_audio(scope, policy, direction="input", source_kind="bytes")


def test_recording_separate_from_import():
    scope = SimpleNamespace(
        enabled=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=False,
    )
    policy = CapturePolicy(enabled=True)
    admit_audio(scope, policy, direction="mixed", source_kind="owned_recording_file")
    with pytest.raises(AttachmentError, match="recording_disabled"):
        admit_audio(
            scope,
            policy,
            direction="mixed",
            source_kind="bytes",
            creates_recording=True,
        )


def test_default_off_creates_nothing(setup, settings):
    settings.FI_ATTACHMENTS_ENABLED = False
    with pytest.raises(AttachmentError, match="disabled"):
        session(setup)
    assert not broker.CaptureSession.objects.exists()


def test_session_idempotency_and_exact_binding(setup):
    client_id = uuid.uuid4()
    first, _ = session(setup, client_id=client_id)
    second, _ = session(setup, client_id=client_id)
    assert first.id == second.id
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        session(setup, client_id=client_id, parent_span_id="ffffffffffffffff")


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
        "state": "pending_upload"
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
    current, token = session((scope, writer))
    broker.authorize(token, operation="create", scope_id=scope.id)
    grant.enabled = False
    grant.save(update_fields=["enabled"])
    with pytest.raises(AttachmentError, match="credential_scope_unsupported"):
        broker.authorize(token, operation="create", scope_id=scope.id)
    assert current.writer_id == key.id


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
    with pytest.raises(AttachmentError, match="scope_unresolved"):
        broker.resolve_scope(writer, project_name="missing", project_type="experiment")
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
    second, _, _, _ = initialized(
        (other, writer), **{k: v for k, v in args.items() if k != "scope_id"}
    )
    assert first.id != second.id and first.client_id == second.client_id
    with pytest.raises(AttachmentError, match="not_found"):
        uploads.status(token, scope_id=other.id, upload_id=second.id)


def test_upload_idempotency_and_conflict(setup):
    first, _, token, args = initialized(setup)
    second, _ = uploads.initialize(token, **args)
    assert first.id == second.id
    assert UploadReservation.objects.count() == 1
    with pytest.raises(AttachmentError, match="idempotency_conflict"):
        uploads.initialize(
            token, **{**args, "expected_bytes": args["expected_bytes"] + 1}
        )


@pytest.mark.parametrize(
    "chunks,code",
    [
        ([b"a" * (uploads.CHUNK_BYTES + 1)], "invalid_chunk"),
        ([wav_bytes() + b"x"], "too_large"),
        ([b"short"], "length_mismatch"),
    ],
)
def test_malformed_transfer_releases_once(setup, chunks, code):
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
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[wav_bytes()],
        storage=storage,
    )
    with pytest.raises(AttachmentError, match="transfer_conflict"):
        uploads.transfer(
            token,
            scope_id=upload.scope_id,
            upload_id=upload.id,
            chunks=[wav_bytes()],
            storage=storage,
        )
    uploads.abort(token, scope_id=upload.scope_id, upload_id=upload.id)
    with pytest.raises(AttachmentError, match="incomplete_upload"):
        uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)
    assert not AttachmentBlob.objects.exists()


@pytest.mark.parametrize(
    "index,field,maximum",
    [
        (0, "active_transfers", 128),
        (1, "reserved_bytes", 2 * 1024**3),
        (2, "active_transfers", 16),
        (2, "validation_jobs", 1024),
        (2, "initialization_attempts", 120),
    ],
)
def test_finite_aggregate_admission(setup, index, field, maximum):
    scope, _ = setup
    key = uploads._budget_keys(scope)[index]
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

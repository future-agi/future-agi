"""Completed-recording import, provider observation and derivatives on PG.

Storage and the remote source are explicit doubles; the sandbox, budgets,
fences, manifests and database triggers are real. Doubles do not qualify a
deployment backend or a provider connector.
"""

import base64
import hashlib
import io
import json
import struct
import sys
import time
import uuid
import wave
from datetime import timedelta

import jwt
import pytest
from django.db import connection as db_connection
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentScope,
)
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentProviderRecording,
    AttachmentRecording,
    AttachmentRecordingDerivative,
    AttachmentRecordingSegment,
    AttachmentSourceEnvelope,
    RecordingSegmentState,
)
from tracer.services.attachments import bootstrap as broker
from tracer.services.attachments import (
    derivatives,
    envelopes,
    imports,
    provider_recordings,
    retention,
)
from tracer.services.attachments.contract import AttachmentError, CapturePolicy
from tracer.services.attachments.storage import ObjectIdentity

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp"),
]

TRACE = "00112233445566778899aabbccddeeff"
SPAN = "0123456789abcdef"
KEY = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
CANARY = "canary-signature-7f3a9"


class Store:
    """Versioned private-store double with the PrivateMediaStorage surface."""

    def __init__(self):
        self.objects, self.erased = {}, []

    def verify_readiness(self):
        return None

    def promote(self, snapshot, *, upload_id, expected_length, expected_sha256):
        snapshot.seek(0)
        data = snapshot.read()
        assert len(data) == expected_length
        assert hashlib.sha256(data).hexdigest() == expected_sha256
        key = f"original/{upload_id}"
        self.objects[key] = data
        return ObjectIdentity(key, "immutable-v1")

    def open_range(self, identity, *, start, end, expected_length):
        data = self.objects[identity.key]
        assert len(data) == expected_length
        return io.BytesIO(data[start : end + 1])

    def erase_upload(self, upload_id):
        self.erased.append(str(upload_id))
        self.objects.pop(f"original/{upload_id}", None)


class MemorySource:
    def __init__(self, data, window):
        self.data, self.window, self.reads = data, window, []

    def read_range(self, offset, length, sink):
        assert 0 < length <= self.window
        self.reads.append((offset, length))
        sink.write(self.data[offset : offset + length])


def wav_bytes(*, channels=2, rate=8000, seconds=65):
    frames = rate * seconds
    samples = []
    for frame in range(frames):
        samples.append(frame % 1000)
        if channels == 2:
            samples.append(-(frame % 1000) - 1)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return stream.getvalue()


@pytest.fixture
def env(project, user, settings, tmp_path):
    settings.FI_ATTACHMENTS_ENABLED = True
    settings.FI_ATTACHMENTS_ENVELOPE_KEYS = f"k1:{KEY}"
    settings.FI_ATTACHMENTS_IMPORT_HOSTS = ("media.example.com",)
    settings.FI_ATTACHMENTS_WORK_DIR = str(tmp_path)
    scope = AttachmentScope.objects.create(
        project=project,
        workspace=project.workspace,
        organization=project.organization,
        enabled=True,
        require_media_redaction=False,
    )
    writer = broker.Writer("user", user.pk, scope.organization_id, scope.workspace_id)
    return scope, writer, imports.Importer("user", user.pk), user


def session(env, **policy):
    scope, writer, _, _ = env
    values = {"enabled": True, "allow_import": True, **policy}
    current, _ = broker.bootstrap(
        writer,
        scope_id=scope.id,
        client_id=uuid.uuid4(),
        trace_id=TRACE,
        parent_span_id=SPAN,
        policy=CapturePolicy(**values),
    )
    return current


def envelope(env, data, *, url=None, direction="mixed"):
    scope, _, importer, _ = env
    return envelopes.seal(
        scope=scope,
        url=url or f"https://cdn.media.example.com/rec.wav?sig={CANARY}",
        creator_kind=importer.kind,
        creator_id=importer.id,
        direction=direction,
        source_bytes=len(data),
    )


def body(current, *, data, envelope_id, recording_id=None, **changes):
    scope = AttachmentScope.objects.get(pk=current.scope_id)
    frames = (len(data) - 44) // (struct.unpack("<H", data[22:24])[0] * 2)
    rate = struct.unpack("<I", data[24:28])[0]
    recording = {
        "schema_version": 1,
        "association": {
            "capture_session_id": str(current.client_id),
            "correlation_nonce": str(current.correlation_nonce),
            "parent": {"trace_id": TRACE, "span_id": SPAN},
        },
        "policy_version": imports.policy_token(scope),
        "deletion_generation": scope.deletion_generation,
        "mime_type": "audio/wav",
        "direction": "mixed",
        "source_kind": "RemoteUrl",
        "recording_id": str(recording_id or uuid.uuid4()),
        "modality": "audio",
        "declared_bytes": len(data),
        "declared_duration_ms": -(-frames * 1000 // rate),
        **changes,
    }
    return {
        "mode": "completed_recording",
        "recording": recording,
        "source": {"kind": "RemoteUrlEnvelope", "envelope_id": str(envelope_id)},
    }


def budgets():
    return {
        row.key: (
            row.reserved_bytes,
            row.active_transfers,
            row.active_recordings,
            row.validation_jobs,
            row.object_count,
            row.link_count,
        )
        for row in AttachmentBudget.objects.all()
    }


def drive(job, store, data):
    return imports.run(
        job.id,
        scope_id=job.scope_id,
        storage=store,
        source_factory=lambda j, limits: MemorySource(data, limits.window),
    )


def test_completed_import_publishes_bounded_segments_once(env):
    data = wav_bytes()
    current = session(env)
    store = Store()
    request = body(current, data=data, envelope_id=envelope(env, data))
    response, job = imports.submit(env[2], scope_id=env[0].id, body=request)
    assert response["state"] == "importing" and set(response) == {
        "mode",
        "job_id",
        "recording_id",
        "state",
    }
    assert set(budgets().values()) == {(len(data) + 8 * 1024 * 1024, 1, 1, 1, 2, 1)}
    assert drive(job, store, data) == "ready"
    recording = AttachmentRecording.objects.get(pk=job.recording_id)
    manifest = recording.manifest
    assert manifest["layout"] == "stereo" and manifest["capture_complete"] is True
    assert [s["duration_ms"] for s in manifest["segments"]] == [30000, 30000, 5000]
    assert manifest["duration_ms"] == 65000
    occurrences = AttachmentOccurrence.objects.filter(scope=env[0])
    assert occurrences.count() == 3
    # Published but unbound: readable only after ingestion binds retention.
    assert all(o.bound_at is None for o in occurrences)
    assert {str(o.client_id) for o in occurrences} == {
        s["occurrence_id"] for s in manifest["segments"]
    }
    # Every reservation released exactly once; the envelope is destroyed.
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}
    assert not AttachmentSourceEnvelope.objects.exists()
    # Replays never re-run or re-reserve.
    assert drive(job, store, data) == "ready"
    replay, same = imports.submit(env[2], scope_id=env[0].id, body=request)
    assert same.pk == job.pk and replay == response
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}
    assert imports.status(env[2], scope_id=env[0].id, job_id=job.id) == {
        "job_id": str(job.id),
        "state": "ready",
        "reason": "ready",
    }
    # Each produced segment is an independently valid WAV of the right audio.
    first = AttachmentRecordingSegment.objects.get(recording=recording, index=0)
    with wave.open(io.BytesIO(store.objects[first.storage_key])) as reader:
        assert (reader.getnchannels(), reader.getframerate()) == (2, 8000)
        assert reader.getnframes() == 8000 * 30


def test_changed_request_conflicts_and_other_importer_sees_nothing(env, user):
    data = wav_bytes(seconds=2)
    current = session(env)
    request = body(current, data=data, envelope_id=envelope(env, data))
    _, job = imports.submit(env[2], scope_id=env[0].id, body=request)
    changed = json.loads(json.dumps(request))
    changed["recording"]["direction"] = "output"
    with pytest.raises(AttachmentError, match="conflict"):
        imports.submit(env[2], scope_id=env[0].id, body=changed)
    with pytest.raises(AttachmentError, match="not_found"):
        imports.status(
            imports.Importer("user", uuid.uuid4()), scope_id=env[0].id, job_id=job.id
        )


@pytest.mark.parametrize(
    ("policy", "code"),
    [
        ({"allow_import": False}, "source_unavailable"),
        ({"hide_outputs": True}, "policy_denied"),
        ({"hide_inputs": True}, "policy_denied"),
        ({"capture_content": False}, "policy_denied"),
        ({"pii_redaction": True}, "redaction_unavailable"),
    ],
)
def test_deny_wins_before_any_fetch_or_reservation(env, policy, code):
    data = wav_bytes(seconds=2)
    current = session(env, **policy)
    request = body(current, data=data, envelope_id=envelope(env, data))
    with pytest.raises(AttachmentError) as error:
        imports.submit(env[2], scope_id=env[0].id, body=request)
    assert error.value.code == code
    assert not AttachmentImportJob.objects.exists()
    assert set(budgets().values()) <= {(0, 0, 0, 0, 0, 0)}


def test_client_label_cannot_understate_trusted_mixed_ancestry(env):
    data = wav_bytes(seconds=2)
    current = session(env, hide_inputs=True)
    request = body(
        current, data=data, envelope_id=envelope(env, data), direction="output"
    )
    # Trusted source is mixed: hiding inputs denies even an "output" label.
    with pytest.raises(AttachmentError, match="policy_denied"):
        imports.submit(env[2], scope_id=env[0].id, body=request)


def test_exhausted_capacity_is_throttled_without_fetching(env):
    data = wav_bytes(seconds=2)
    current = session(env)
    scope = env[0]
    AttachmentBudget.objects.create(
        key=f"project:{scope.project_id}",
        window_started_at=timezone.now(),
        active_recordings=4,
    )
    request = body(current, data=data, envelope_id=envelope(env, data))
    with pytest.raises(AttachmentError, match="throttled"):
        imports.submit(env[2], scope_id=scope.id, body=request)
    assert not AttachmentImportJob.objects.exists()


def test_envelope_bound_to_creator_scope_and_ttl(env, user):
    data = wav_bytes(seconds=2)
    current = session(env)
    scope = env[0]
    foreign = envelopes.seal(
        scope=scope,
        url="https://cdn.media.example.com/x",
        creator_kind="user",
        creator_id=uuid.uuid4(),
        direction="mixed",
        source_bytes=len(data),
    )
    with pytest.raises(AttachmentError, match="source_unavailable"):
        imports.submit(
            env[2],
            scope_id=scope.id,
            body=body(current, data=data, envelope_id=foreign),
        )
    expired = envelope(env, data)
    AttachmentSourceEnvelope.objects.filter(pk=expired).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(AttachmentError, match="source_unavailable"):
        imports.submit(
            env[2],
            scope_id=scope.id,
            body=body(current, data=data, envelope_id=expired),
        )
    assert envelopes.sweep() == 1
    row = AttachmentSourceEnvelope.objects.get(pk=envelope(env, data))
    assert row.expires_at <= timezone.now() + timedelta(seconds=600)
    assert CANARY.encode() not in bytes(row.ciphertext)


def _dump_attachment_tables():
    with db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE 'tracer_attachment%%'"
            " OR tablename IN ('tracer_capturesession', 'tracer_uploadreservation')"
        )
        dump = []
        for (table,) in cursor.fetchall():
            cursor.execute(f'SELECT row_to_json(t)::text FROM "{table}" t')  # noqa: S608
            dump += [row[0] for row in cursor.fetchall()]
    return "\n".join(dump)


def test_unapproved_destination_dead_letters_without_secret_canaries(env, settings):
    settings.FI_ATTACHMENTS_IMPORT_HOSTS = ("other.example.com",)
    data = wav_bytes(seconds=2)
    current = session(env)
    request = body(current, data=data, envelope_id=envelope(env, data))
    _, job = imports.submit(env[2], scope_id=env[0].id, body=request)
    store = Store()
    states = [
        imports.run(job.id, scope_id=job.scope_id, storage=store) for _ in range(2)
    ]
    # A policy denial is final at once: no retry ever contacts the host.
    assert states == ["rejected", "rejected"]
    job.refresh_from_db()
    assert job.attempts == 1 and job.reason == "source_unavailable"
    dump = _dump_attachment_tables()
    assert CANARY not in dump and "cdn.media.example.com" not in dump
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}
    assert not AttachmentSourceEnvelope.objects.exists()


def test_transient_transport_errors_retry_at_most_three_times(env):
    data = wav_bytes(seconds=2)
    current = session(env)
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )

    def failing(job_row, limits):
        raise AttachmentError("source_unavailable", 503)

    states = [
        imports.run(
            job.id, scope_id=job.scope_id, storage=Store(), source_factory=failing
        )
        for _ in range(4)
    ]
    assert states == ["importing", "importing", "failed", "failed"]
    job.refresh_from_db()
    assert job.attempts == 3 and job.reason == "source_unavailable"
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}


@pytest.mark.parametrize(
    "tamper",
    [
        lambda data: data[:-2],  # short source
        lambda data: data[:40] + struct.pack("<I", 7) + data[44:],  # header lies
        lambda data: b"OggS" + data[4:],  # not the declared container
    ],
)
def test_corrupt_or_short_source_rejected_and_staged_parts_erased(env, tamper):
    data = wav_bytes(seconds=2)
    current = session(env)
    request = body(current, data=data, envelope_id=envelope(env, data))
    _, job = imports.submit(env[2], scope_id=env[0].id, body=request)
    bad = tamper(data)
    state = imports.run(
        job.id,
        scope_id=job.scope_id,
        storage=Store(),
        source_factory=lambda j, limits: MemorySource(bad, limits.window),
    )
    assert state == "rejected"
    assert not AttachmentOccurrence.objects.exists()
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}


def test_deletion_during_import_fences_publication(env, monkeypatch):
    data = wav_bytes(seconds=65)
    current = session(env)
    request = body(current, data=data, envelope_id=envelope(env, data))
    _, job = imports.submit(env[2], scope_id=env[0].id, body=request)
    store = Store()
    original = imports._reserve_segment
    calls = []

    def reserve(job_row, size):
        calls.append(size)
        if len(calls) == 2:
            retention.fence_scope(scope_id=job_row.scope_id)
        return original(job_row, size)

    monkeypatch.setattr(imports, "_reserve_segment", reserve)
    assert drive(job, store, data) == "deleted"
    assert not AttachmentOccurrence.objects.exists()
    assert store.objects == {}
    assert (
        AttachmentRecordingSegment.objects.filter(
            state=RecordingSegmentState.STAGED
        ).count()
        == 0
    )
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}


def test_deadline_sweep_expires_and_releases_once(env):
    data = wav_bytes(seconds=2)
    current = session(env)
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )
    AttachmentImportJob.objects.filter(pk=job.pk).update(
        deadline=timezone.now() - timedelta(seconds=1)
    )
    assert imports.sweep(storage=Store()) == 1
    assert imports.sweep(storage=Store()) == 0
    job.refresh_from_db()
    assert job.state == "expired"
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}


# ---------------------------------------------------------------- provider


def livekit(env, secret="s" * 32, key="APIkey123"):
    scope, _, _, user = env
    return provider_recordings.configure_livekit(
        scope=scope, user=user, api_key_id=key, api_secret=secret
    )


def signed(payload, *, secret="s" * 32, key="APIkey123"):
    raw = json.dumps(payload).encode()
    claims = {
        "iss": key,
        "exp": int(time.time()) + 300,
        "nbf": int(time.time()) - 5,
        "sha256": base64.b64encode(hashlib.sha256(raw).digest()).decode(),
    }
    return raw, jwt.encode(claims, secret, algorithm="HS256")


def event(event_id, status, *, egress="EG_abc123", room="room-1", updated=1, size=0):
    info = {
        "egressId": egress,
        "roomName": room,
        "status": status,
        "updatedAt": str(updated),
    }
    if status == "EGRESS_COMPLETE":
        info["fileResults"] = [
            {
                "filename": "secret-name.wav",
                "location": f"https://bucket.example/x?sig={CANARY}",
                "size": str(size),
                "duration": str(65 * 1_000_000_000),
            }
        ]
    kind = "egress_ended" if status == "EGRESS_COMPLETE" else "egress_updated"
    return {"event": kind, "id": event_id, "egressInfo": info}


def registered(env, connection, current, *, egress="EG_abc123"):
    return provider_recordings.register(
        env[2],
        scope_id=env[0].id,
        connection_id=connection.id,
        egress_id=egress,
        room_name="room-1",
        recording_id=uuid.uuid4(),
        association={
            "capture_session_id": str(current.client_id),
            "correlation_nonce": str(current.correlation_nonce),
            "parent": {"trace_id": TRACE, "span_id": SPAN},
        },
        direction="mixed",
    )


def deliver(payload, **sign):
    raw, token = signed(payload, **sign)
    connection, verified = provider_recordings.verify_livekit_webhook(raw, token)
    return provider_recordings.process_livekit_event(connection, verified)


def test_webhook_signature_body_issuer_and_revocation(env):
    connection = livekit(env)
    raw, token = signed(event("EV_1", "EGRESS_ACTIVE"))
    assert provider_recordings.verify_livekit_webhook(raw, token)[0] == connection
    for body_bytes, auth in (
        (raw + b" ", token),  # body tampered
        (raw, signed(event("EV_1", "EGRESS_ACTIVE"), secret="x" * 32)[1]),
        (raw, signed(event("EV_1", "EGRESS_ACTIVE"), key="APIother")[1]),
        (raw, ""),
        (raw, jwt.encode({"iss": "APIkey123"}, "s" * 32, algorithm="HS256")),
    ):
        with pytest.raises(AttachmentError, match="not_found"):
            provider_recordings.verify_livekit_webhook(body_bytes, auth)
    provider_recordings.revoke(scope_id=env[0].id, connection_id=connection.id)
    with pytest.raises(AttachmentError, match="not_found"):
        provider_recordings.verify_livekit_webhook(raw, token)


def test_out_of_order_duplicate_wrong_room_and_foreign_egress(env):
    connection = livekit(env)
    current = session(env)
    data = wav_bytes(seconds=65)
    registration = registered(env, connection, current)
    deliver(event("EV_2", "EGRESS_COMPLETE", updated=5, size=len(data)))
    registration.refresh_from_db()
    assert registration.provider_state == "complete"
    assert registration.output_duration_ms == 65000
    # Late "active" and duplicate deliveries never regress a terminal state.
    deliver(event("EV_1", "EGRESS_ACTIVE", updated=1))
    deliver(event("EV_2", "EGRESS_FAILED", updated=9))
    registration.refresh_from_db()
    assert registration.provider_state == "complete"
    # Wrong room / unknown egress / other tenant's egress change nothing.
    other = registered(env, connection, current, egress="EG_other")
    deliver(event("EV_3", "EGRESS_COMPLETE", egress="EG_other", room="room-2", size=9))
    deliver(event("EV_4", "EGRESS_COMPLETE", egress="EG_unknown", size=9))
    other.refresh_from_db()
    assert other.provider_state == "registered"
    dump = _dump_attachment_tables()
    assert CANARY not in dump and "secret-name" not in dump and "room-1" not in dump


def test_completion_without_qualified_reader_fetches_nothing(env):
    connection = livekit(env)
    current = session(env)
    registration = registered(env, connection, current)
    deliver(event("EV_9", "EGRESS_COMPLETE", size=1000))
    registration.refresh_from_db()
    assert registration.import_job_id is None
    assert not AttachmentImportJob.objects.exists()


def test_completed_egress_enters_validation_exactly_once(env, monkeypatch):
    data = wav_bytes(seconds=65)
    monkeypatch.setitem(
        provider_recordings.READERS,
        "livekit",
        lambda registration, limits: MemorySource(data, limits.window),
    )
    connection = livekit(env)
    current = session(env)
    registration = registered(env, connection, current)
    deliver(event("EV_5", "EGRESS_COMPLETE", size=len(data)))
    deliver(event("EV_6", "EGRESS_COMPLETE", size=len(data), updated=7))

    class Status:
        def get(self, egress_id):
            return event("x", "EGRESS_COMPLETE", size=len(data))["egressInfo"]

    provider_recordings.reconcile(registration, Status())
    assert AttachmentImportJob.objects.count() == 1
    job = AttachmentImportJob.objects.get()
    assert imports.run(job.id, scope_id=job.scope_id, storage=Store()) == "ready"
    # Revoked connections stop retrieval and reconciliation.
    provider_recordings.revoke(scope_id=env[0].id, connection_id=connection.id)
    with pytest.raises(AttachmentError, match="source_unavailable"):
        provider_recordings.reconcile(registration, Status())


def test_reconcile_with_unavailable_status_api_changes_nothing(env):
    connection = livekit(env)
    current = session(env)
    registration = registered(env, connection, current)

    class Unavailable:
        def get(self, egress_id):
            return None

    assert provider_recordings.reconcile(
        registration, Unavailable()
    ).provider_state == ("registered")
    assert AttachmentProviderRecording.objects.get().import_job_id is None


# ------------------------------------------------------------- derivatives


def test_stereo_derivatives_isolate_channels_and_fence_on_deletion(env):
    data = wav_bytes(seconds=35)
    current = session(env)
    store = Store()
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )
    assert drive(job, store, data) == "ready"
    segments = list(
        AttachmentRecordingSegment.objects.filter(recording_id=job.recording_id)
    )
    for segment in segments:
        derivatives.generate(segment.pk, scope_id=segment.scope_id, storage=store)
        derivatives.generate(segment.pk, scope_id=segment.scope_id, storage=store)
    assert AttachmentRecordingDerivative.objects.count() == 3 * len(segments)
    second = segments[1]
    for channel, sign in ((0, 1), (1, -1)):
        identity, length, _ = derivatives.identity_for(
            scope_id=env[0].id,
            occurrence_id=second.occurrence_client_id,
            representation=f"channel-{channel}",
        )
        with wave.open(io.BytesIO(store.objects[identity.key])) as reader:
            assert reader.getnchannels() == 1
            frames = reader.getnframes()
            samples = struct.unpack(f"<{frames}h", reader.readframes(frames))
        start = second.start_frame
        expected = [
            (start + i) % 1000 if sign > 0 else -((start + i) % 1000) - 1
            for i in range(frames)
        ]
        assert list(samples) == expected  # wrong speaker/channel mapping fails
    peaks = derivatives.identity_for(
        scope_id=env[0].id,
        occurrence_id=second.occurrence_client_id,
        representation="peaks",
    )
    assert peaks[1] == derivatives.peaks_bytes(
        frame_count=second.frame_count, rate=8000, channels=2
    )
    assert derivatives.recording_manifest(
        scope_id=env[0].id,
        recording_id=AttachmentRecording.objects.get(pk=job.recording_id).client_id,
    )
    retention.fence_scope(scope_id=env[0].id)
    assert (
        derivatives.identity_for(
            scope_id=env[0].id,
            occurrence_id=second.occurrence_client_id,
            representation="channel-0",
        )
        is None
    )
    with pytest.raises(AttachmentError, match="validation_fenced"):
        derivatives.generate(segments[0].pk, scope_id=env[0].id, storage=store)


def test_published_segments_survive_read_path_readmission_once_bound(env, user):
    """occurrences.get_occurrence re-runs admit_audio with the stored kind."""
    from django.db import transaction

    from tracer.services.attachments import occurrences

    data = wav_bytes(seconds=35)
    current = session(env)
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )
    assert drive(job, Store(), data) == "ready"
    scope = env[0]
    parent = occurrences.Parent(uuid.UUID(TRACE), SPAN)
    state = occurrences.SourceState(
        scope.id,
        parent,
        1,
        timezone.now() - timedelta(minutes=5),
        timezone.now() + timedelta(hours=1),
        True,
    )
    viewer = occurrences.Viewer(
        user.id, uuid.uuid4(), scope.organization_id, scope.workspace_id,
        scope.project_id,
    )  # fmt: skip

    class Authority:
        def session_is_live(self, candidate):
            return candidate == viewer

        def source(self, candidate, scope_row, wanted):
            return state if wanted == parent else None

    rows = list(AttachmentOccurrence.objects.filter(scope=scope))
    assert {row.attachment.source_kind for row in rows} == {"imported_recording"}
    for row in rows:
        with pytest.raises(AttachmentError):  # unbound: not readable yet
            occurrences.get_occurrence(viewer, Authority(), row.client_id, parent)
        with transaction.atomic():
            retention.bind_retention(scope_id=scope.id, occurrence_id=row.id, source=state)
        found, _, _ = occurrences.get_occurrence(
            viewer, Authority(), row.client_id, parent, ready=True
        )
        assert found.pk == row.pk

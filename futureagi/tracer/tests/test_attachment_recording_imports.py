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
from django.db.models import F
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentScope,
    CaptureSession,
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
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.storage import ObjectIdentity
from tracer.tests.test_attachment_api import api  # noqa: F401  (HTTP fixture)

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


def set_allow_import(scope, value):
    """Flip the opt-in the way the 0133 scope fence requires: with a policy bump."""
    AttachmentScope.objects.filter(pk=scope.pk).exclude(allow_import=value).update(
        allow_import=value, policy_version=F("policy_version") + 1
    )


def session(env, **policy):
    """A live capture session of the env writer, bootstrapped through F01.

    ``allow_import`` is the scope switch. Other keys are client-side
    restrictions (hide/PII); a session snapshot carrying them is stored as-is
    so the server's deny-wins re-check is exercised on every later step.
    """
    scope, writer, _, _ = env
    set_allow_import(scope, policy.pop("allow_import", True))
    current, _ = broker.bootstrap(
        writer,
        {
            "schema_versions": [1],
            "project_name": scope.project.name,
            "project_type": scope.project.trace_type,
            "capture_session_id": str(uuid.uuid4()),
            "correlation_nonce": str(uuid.uuid4()),
        },
    )
    if not policy:
        return current
    return CaptureSession.objects.create(
        scope_id=current.scope_id,
        client_id=uuid.uuid4(),
        correlation_nonce=uuid.uuid4(),
        writer_kind=current.writer_kind,
        writer_id=current.writer_id,
        capture_policy={**current.capture_policy, **policy},
        policy_version=current.policy_version,
        deletion_generation=current.deletion_generation,
        expires_at=current.expires_at,
        request_fingerprint=uuid.uuid4().hex,
    )


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

    def reserve(job_row, size, token=None):
        calls.append(size)
        if len(calls) == 2:
            retention.fence_scope(scope_id=job_row.scope_id)
        return original(job_row, size, token)

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


# ------------------------------------------- staged objects of finished jobs

IDLE = (0, 0, 0, 0, 0, 0)


class StrictStore(Store):
    """Like the real store: ``promote`` never overwrites a key (IfNoneMatch)."""

    def promote(self, snapshot, *, upload_id, **kwargs):
        assert f"original/{upload_id}" not in self.objects, "key already exists"
        return super().promote(snapshot, upload_id=upload_id, **kwargs)


class RejectThird(StrictStore):
    """The third staged part fails validation: a terminal, non-retried reject."""

    def __init__(self):
        super().__init__()
        self.promoted = 0

    def promote(self, snapshot, **kwargs):
        self.promoted += 1
        if self.promoted == 3:
            raise AttachmentError("validation_failed", 422)
        return super().promote(snapshot, **kwargs)


class EraseFailsOnce(RejectThird):
    def __init__(self):
        super().__init__()
        self.failures = 1

    def erase_upload(self, upload_id):
        if self.failures:
            self.failures -= 1
            # What PrivateMediaStorage.erase_upload raises on a storage error.
            raise AttachmentError("cleanup_unavailable", 503)
        return super().erase_upload(upload_id)


def _submitted(env, data):
    current = session(env)
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )
    return job


def _segment_states(job):
    return [
        (row.index, row.state)
        for row in AttachmentRecordingSegment.objects.filter(
            recording_id=job.recording_id
        ).order_by("index")
    ]


def test_rejected_import_erases_every_staged_object_before_release(env):
    store = RejectThird()
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    assert drive(job, store, data) == "rejected"
    job.refresh_from_db()
    assert job.released_at is not None
    # The part whose write failed was allocated first, so it is erased too.
    assert _segment_states(job) == [(0, "erased"), (1, "erased"), (2, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}


def test_an_erase_failure_in_terminal_cleanup_holds_the_release_until_the_sweep(
    env,
):
    store = EraseFailsOnce()
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    # The storage error never escapes the run (it would end the tick).
    assert drive(job, store, data) == "rejected"
    job.refresh_from_db()
    assert job.released_at is None
    assert _segment_states(job) == [(0, "staged"), (1, "erased"), (2, "erased")]
    assert len(store.objects) == 1
    assert set(budgets().values()) != {IDLE}
    # Nothing else schedules a release of this job's reservation early.
    assert imports.pending(limit=10) == []
    assert imports.sweep(storage=store) == 1
    job.refresh_from_db()
    assert job.released_at is not None
    assert _segment_states(job) == [(0, "erased"), (1, "erased"), (2, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}
    assert imports.sweep(storage=store) == 0


def test_a_released_job_with_a_staged_object_left_is_still_erased(env):
    """Rows left ``staged`` by an older release are found regardless of it."""
    store = EraseFailsOnce()
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    assert drive(job, store, data) == "rejected"
    imports._release(job)  # as the pre-fix sweep did, with bytes still stored
    assert len(store.objects) == 1
    assert imports.sweep(storage=store) == 0  # nothing left to release
    assert _segment_states(job) == [(0, "erased"), (1, "erased"), (2, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}


def test_sweep_expiring_a_job_while_its_worker_writes_a_segment(env):
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    seen = {}

    class SweptDuringWrite(StrictStore):
        def promote(inner, snapshot, **kwargs):
            if len(inner.objects) == 1:
                # The 300 s sweep lands while segment 1's object is in flight.
                AttachmentImportJob.objects.filter(pk=job.pk).update(
                    deadline=timezone.now() - timedelta(seconds=1)
                )
                seen["swept"] = imports.sweep(storage=inner)
                row = AttachmentImportJob.objects.get(pk=job.pk)
                seen["state"], seen["released"] = row.state, row.released_at
                seen["segments"] = _segment_states(job)
            return super().promote(snapshot, **kwargs)

    store = SweptDuringWrite()
    assert drive(job, store, data) == "expired"
    # The sweep expired the job, erased the finished part, and kept the
    # in-flight part and the whole reservation while the worker held its lease.
    assert seen["swept"] == 1 and seen["state"] == "expired"
    assert seen["released"] is None
    assert seen["segments"] == [(0, "erased"), (1, "staged")]
    # The worker's next fence sees the expiry; it erases its part and releases.
    job.refresh_from_db()
    assert job.released_at is not None
    assert _segment_states(job) == [(0, "erased"), (1, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}
    assert imports.sweep(storage=store) == 0


def test_sweep_expiring_a_job_before_its_worker_allocates_a_segment(env, monkeypatch):
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    store = StrictStore()
    original = imports._allocate
    calls = []

    def allocate(job_row, *args):
        calls.append(1)
        if len(calls) == 2:
            # Swept while segment 1 was still in the sandbox: no row, no object.
            AttachmentImportJob.objects.filter(pk=job.pk).update(
                deadline=timezone.now() - timedelta(seconds=1)
            )
            assert imports.sweep(storage=store) == 1
        return original(job_row, *args)

    monkeypatch.setattr(imports, "_allocate", allocate)
    assert drive(job, store, data) == "expired"
    # A finished job never gets a new row, so no object appears after release.
    assert _segment_states(job) == [(0, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}


def _crash_after_the_object_write(monkeypatch):
    original = imports._record_object
    crashed = []

    def record(*args):
        if not crashed:
            crashed.append(1)
            raise RuntimeError("worker died after writing the object")
        return original(*args)

    monkeypatch.setattr(imports, "_record_object", record)


def test_a_crash_between_object_write_and_row_update_is_retried_cleanly(
    env, monkeypatch
):
    _crash_after_the_object_write(monkeypatch)
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    store = StrictStore()
    assert drive(job, store, data) == "importing"
    row = AttachmentRecordingSegment.objects.get(recording_id=job.recording_id)
    # The object exists and its row already names it; no version recorded yet.
    assert (row.state, row.storage_version) == ("staged", "")
    assert set(store.objects) == {f"original/{row.attachment_client_id}"}
    # The retry erases that object and writes it again under the same identity.
    assert drive(job, store, data) == "ready"
    row.refresh_from_db()
    assert row.state == "published" and row.storage_version == "immutable-v1"
    assert str(row.attachment_client_id) in store.erased
    assert len(store.objects) == 3
    assert set(budgets().values()) == {IDLE}


def test_a_crash_after_the_object_write_never_strands_it(env, monkeypatch):
    _crash_after_the_object_write(monkeypatch)
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)
    store = StrictStore()
    assert drive(job, store, data) == "importing"
    # A real crash keeps the lease until it lapses; then the deadline passes.
    AttachmentImportJob.objects.filter(pk=job.pk).update(
        lease_until=timezone.now() + timedelta(minutes=5),
        deadline=timezone.now() - timedelta(seconds=1),
    )
    assert imports.sweep(storage=store) == 1
    job.refresh_from_db()
    # The writer might still be uploading: keep the row and the reservation.
    assert job.state == "expired" and job.released_at is None
    assert _segment_states(job) == [(0, "staged")] and len(store.objects) == 1
    AttachmentImportJob.objects.filter(pk=job.pk).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    assert imports.sweep(storage=store) == 1
    job.refresh_from_db()
    assert job.released_at is not None
    assert _segment_states(job) == [(0, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}


def test_a_worker_that_lost_its_row_erases_the_object_it_wrote(env, monkeypatch):
    data = wav_bytes(seconds=65)
    job = _submitted(env, data)

    class ErasedDuringWrite(StrictStore):
        def promote(inner, snapshot, **kwargs):
            if not inner.objects:
                # The worker stalled past its lease and the job was finished
                # and cleaned up meanwhile (the sweep erased its row).
                AttachmentImportJob.objects.filter(pk=job.pk).update(
                    deadline=timezone.now() - timedelta(seconds=1),
                    lease_until=timezone.now() - timedelta(seconds=1),
                )
                assert imports.sweep(storage=inner) == 1
            return super().promote(snapshot, **kwargs)

    store = ErasedDuringWrite()
    assert drive(job, store, data) == "importing"  # lease lost: stop quietly
    job.refresh_from_db()
    assert job.state == "expired" and job.released_at is not None
    assert _segment_states(job) == [(0, "erased")]
    assert store.objects == {}
    assert set(budgets().values()) == {IDLE}


def test_one_failing_import_does_not_end_the_tick(env, settings, monkeypatch):
    from tracer.tasks import attachment_recordings as tasks

    store = Store()
    first, second = (uuid.uuid4(), env[0].id), (uuid.uuid4(), env[0].id)
    ran = []

    def run(job_id, *, scope_id, storage):
        ran.append(job_id)
        if job_id == first[0]:
            raise AttachmentError("cleanup_unavailable", 503)
        return "ready"

    monkeypatch.setattr(tasks, "_ready", lambda: store)
    monkeypatch.setattr(imports, "pending", lambda limit: [first, second])
    monkeypatch.setattr(imports, "run", run)
    monkeypatch.setattr(derivatives, "pending", lambda limit: [])
    tasks.run_attachment_recording_jobs._original_func()
    assert ran == [first[0], second[0]]


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
            retention.bind_retention(
                scope_id=scope.id, occurrence_id=row.id, source=state
            )
        found, _, _ = occurrences.get_occurrence(
            viewer, Authority(), row.client_id, parent, ready=True
        )
        assert found.pk == row.pk


# ------------------------------------------------------- lease / ownership


def test_lease_keeps_one_worker_per_job_and_fences_a_stale_one(env):
    data = wav_bytes(seconds=2)
    current = session(env)
    store = Store()
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )
    stale, why = imports._claim(job)
    assert why is None and imports.pending() == []
    # A second worker while the lease is live does nothing at all.
    assert drive(job, store, data) == "importing"
    job.refresh_from_db()
    assert job.attempts == 1
    assert not AttachmentRecordingSegment.objects.exists()
    # The holder vanished: once its lease lapses another worker takes over.
    AttachmentImportJob.objects.filter(pk=job.pk).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    assert imports.pending() == [(job.pk, job.scope_id)]
    assert drive(job, store, data) == "ready"
    # The stale worker can neither finish, reserve nor publish any more.
    finished = imports._finish(
        job, state="failed", reason="source_unavailable", storage=store, token=stale
    )
    assert finished.state == "ready"
    with pytest.raises(imports._LeaseLost):
        imports._reserve_segment(job, 1, stale)
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}


def test_worker_that_loses_its_lease_mid_run_publishes_nothing(env):
    data = wav_bytes(seconds=35)
    current = session(env)
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )

    def stolen(j, limits):
        # Another worker claims the job while this one is still fetching.
        AttachmentImportJob.objects.filter(pk=j.pk).update(lease_token=uuid.uuid4())
        return MemorySource(data, limits.window)

    state = imports.run(
        job.id, scope_id=job.scope_id, storage=Store(), source_factory=stolen
    )
    assert state == "importing"
    job.refresh_from_db()
    assert job.state == "importing" and job.finished_at is None
    assert not AttachmentOccurrence.objects.exists()
    assert AttachmentRecording.objects.get(pk=job.recording_id).manifest is None


def test_turning_import_off_denies_live_sessions_and_queued_jobs(env):
    data = wav_bytes(seconds=2)
    current = session(env)
    _, job = imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
    )
    set_allow_import(env[0], False)
    with pytest.raises(AttachmentError, match="source_unavailable"):
        imports.submit(
            env[2],
            scope_id=env[0].id,
            body=body(current, data=data, envelope_id=envelope(env, data)),
        )
    assert drive(job, Store(), data) == "rejected"
    assert not AttachmentOccurrence.objects.exists()
    assert set(budgets().values()) == {(0, 0, 0, 0, 0, 0)}


def test_registration_needs_the_registrars_own_live_session(env):
    connection = livekit(env)
    current = session(env)
    association = {
        "capture_session_id": str(current.client_id),
        "correlation_nonce": str(current.correlation_nonce),
        "parent": {"trace_id": TRACE, "span_id": SPAN},
    }
    for registrar, changes in (
        (imports.Importer("user", uuid.uuid4()), {}),  # someone else's session
        (env[2], {"correlation_nonce": str(uuid.uuid4())}),  # wrong nonce
    ):
        with pytest.raises(AttachmentError, match="not_found"):
            provider_recordings.register(
                registrar,
                scope_id=env[0].id,
                connection_id=connection.id,
                egress_id="EG_abc123",
                room_name="room-1",
                recording_id=uuid.uuid4(),
                association={**association, **changes},
                direction="mixed",
            )
    assert not AttachmentProviderRecording.objects.exists()


# ------------------------------------------------------------- HTTP routes


def test_import_routes_admit_report_and_serve_the_manifest(api, settings, tmp_path):  # noqa: F811
    from tracer.tests import test_attachment_api as http

    settings.FI_ATTACHMENTS_ENVELOPE_KEYS = f"k1:{KEY}"
    settings.FI_ATTACHMENTS_IMPORT_HOSTS = ("media.example.com",)
    settings.FI_ATTACHMENTS_WORK_DIR = str(tmp_path)
    scope = api.scope
    imports_path = f"{http.base(api)}/imports"
    data = wav_bytes(seconds=35)

    # Imports are off by default: a uniform refusal, nothing recorded.
    reply, session_body = http.bootstrap(api)
    http.ok(reply, 200, "BootstrapResponse")
    current = CaptureSession.objects.get(
        scope=scope, client_id=session_body["capture_session_id"]
    )
    sealed = envelopes.seal(
        scope=scope,
        url=f"https://cdn.media.example.com/rec.wav?sig={CANARY}",
        creator_kind="user",
        creator_id=api.user.pk,
        direction="mixed",
        source_bytes=len(data),
    )
    request = body(current, data=data, envelope_id=sealed)
    http.assert_error(
        http.post(api, imports_path, request, {**http.auth(api.token), **http.key()}),
        403,
        "policy_denied",
    )
    assert not AttachmentImportJob.objects.exists()

    # Opt in, then bootstrap a session that carries the switch.
    set_allow_import(scope, True)
    reply, session_body = http.bootstrap(api)
    http.ok(reply, 200, "BootstrapResponse")
    current = CaptureSession.objects.get(
        scope=scope, client_id=session_body["capture_session_id"]
    )
    request = body(current, data=data, envelope_id=sealed)
    headers = {**http.auth(api.token), **http.key("im")}
    accepted = http.ok(
        http.post(api, imports_path, request, headers), 202, "ImportResponse"
    )
    assert accepted["mode"] == "completed_recording"
    assert accepted["state"] == "importing"
    replay = http.ok(
        http.post(api, imports_path, request, headers), 202, "ImportResponse"
    )
    assert replay == accepted
    changed = json.loads(json.dumps(request))
    changed["recording"]["direction"] = "output"
    http.assert_error(http.post(api, imports_path, changed, headers), 409, "conflict")
    malformed = json.loads(json.dumps(request))
    malformed["recording"]["declared_duration_ms"] += 1
    malformed["recording"]["recording_id"] = str(uuid.uuid4())
    malformed["recording"]["mime_type"] = "audio/ogg"
    http.assert_error(
        http.post(api, imports_path, malformed, headers), 400, "unsupported_version"
    )

    status_path = f"{imports_path}/{accepted['job_id']}/status"
    queued = http.ok(
        api.client.get(status_path, **http.auth(api.token)),
        200,
        "ImportStatusResponse",
    )
    assert queued == {
        "job_id": accepted["job_id"],
        "state": "importing",
        "reason": "queued",
    }
    http.assert_error(
        api.client.get(f"{imports_path}/{uuid.uuid4()}/status", **http.auth(api.token)),
        404,
        "not_found",
    )

    job = AttachmentImportJob.objects.get(pk=accepted["job_id"])
    assert drive(job, Store(), data) == "ready"
    done = http.ok(
        api.client.get(status_path, **http.auth(api.token)),
        200,
        "ImportStatusResponse",
    )
    assert (done["state"], done["reason"]) == ("ready", "ready")

    # The viewer manifest lists the server-made segments, not gaps.
    manifest_path = f"{http.base(api)}/recordings/{accepted['recording_id']}/manifest"
    parent = {"trace_id": TRACE, "span_id": SPAN}
    manifest = http.ok(
        api.client.get(manifest_path, parent, **http.auth(api.token)),
        200,
        "RecordingManifest",
    )
    assert manifest["capture_complete"] is True
    assert [s["duration_ms"] for s in manifest["segments"]] == [30000, 5000]
    assert all(gap["reason"] != "incomplete" for gap in manifest["gaps"])
    linked = AttachmentOccurrence.objects.filter(
        recording__client_id=accepted["recording_id"]
    )
    assert {str(o.client_id) for o in linked} == {
        s["occurrence_id"] for s in manifest["segments"]
    }
    assert {o.purpose for o in linked} == {"conversation_recording"}
    # A deleted segment occurrence is never served: it becomes a gap.
    first = manifest["segments"][0]["occurrence_id"]
    AttachmentOccurrence.objects.filter(scope=scope, client_id=first).update(
        deleted_at=timezone.now()
    )
    after = http.ok(
        api.client.get(manifest_path, parent, **http.auth(api.token)),
        200,
        "RecordingManifest",
    )
    assert [s["occurrence_id"] for s in after["segments"]] == [
        manifest["segments"][1]["occurrence_id"]
    ]
    assert after["capture_complete"] is False
    assert {"start_ms": 0, "duration_ms": 30000, "reason": "incomplete"} in [
        {k: g[k] for k in ("start_ms", "duration_ms", "reason")} for g in after["gaps"]
    ]
    dump = _dump_attachment_tables()
    assert CANARY not in dump

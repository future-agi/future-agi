"""Database-free execution of the import service with deterministic ORM doubles.

These tests exercise service control flow, not PostgreSQL locks/triggers. Real
concurrency, native storage and full-app publication remain integration gates.
"""

import copy
import importlib
import sys
import types
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1] / "services" / "attachments"


class Row:
    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)
        if hasattr(value, "pk"):
            object.__setattr__(self, key + "_id", value.id)

    def __init__(self, **values):
        self.__dict__.update(values)
        self.id = values.get("id", uuid4())
        self.pk = self.id
        for key, value in values.items():
            if hasattr(value, "pk"):
                setattr(self, key + "_id", value.id)

    def save(self, **_kwargs):
        if self not in self.__class__.objects.rows:
            self.__class__.objects.rows.append(self)
        self._persisted = True

    def refresh_from_db(self):
        pass


def attr(row, key):
    for part in key.split("__"):
        row = getattr(row, part)
    return row


class Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, **values):
        def matches(row):
            for key, value in values.items():
                if key.endswith("__in"):
                    if attr(row, key[:-4]) not in value:
                        return False
                else:
                    actual = attr(row, key)
                    if isinstance(actual, UUID) and isinstance(value, str):
                        value = UUID(value)
                    if actual != value:
                        return False
            return True

        return Query([row for row in self.rows if matches(row)])

    def get(self, **values):
        rows = self.filter(**values).rows
        assert len(rows) == 1, (values, len(rows))
        return rows[0]

    def first(self):
        return self.rows[0] if self.rows else None

    def count(self):
        return len(self.rows)

    def exists(self):
        return bool(self.rows)

    def select_for_update(self):
        return self

    def order_by(self, field):
        return Query(sorted(self.rows, key=lambda row: attr(row, field)))

    def __iter__(self):
        return iter(self.rows)


class Manager(Query):
    def __init__(self, model):
        super().__init__([])
        self.model = model

    def create(self, **values):
        row = self.model(**values)
        row.save()
        return row


def model(name, **defaults):
    class Model(Row):
        def __init__(self, **values):
            for key in ("client_id", "correlation_nonce"):
                if isinstance(values.get(key), str):
                    values[key] = UUID(values[key])
            super().__init__(**{**copy.deepcopy(defaults), **values})

    Model.__name__ = name
    Model.objects = Manager(Model)
    return Model


@pytest.fixture
def lane(monkeypatch, tmp_path):
    from django.conf import settings

    if not settings.configured:
        settings.configure(USE_TZ=True)
    for name, path in (
        ("tracer", ROOT.parents[1]),
        ("tracer.services", ROOT.parent),
        ("tracer.services.attachments", ROOT),
    ):
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, package)
    # Isolate previous tests and avoid importing the full application's app registry.
    for name in tuple(sys.modules):
        if name.startswith("tracer.services.attachments."):
            monkeypatch.delitem(sys.modules, name)
    models = types.ModuleType("tracer.models.attachment")
    record_models = types.ModuleType("tracer.models.attachment_recording")
    terminal = {"ready", "failed", "rejected", "expired", "deleted"}
    state = NS(
        READY="ready",
        PENDING="pending_upload",
        VALIDATING="validating",
        IMPORTING="importing",
        FAILED="failed",
        REJECTED="rejected",
        EXPIRED="expired",
        DELETED="deleted",
    )
    for name in (
        "Attachment",
        "AttachmentOutbox",
        "AttachmentManifestRevision",
        "AttachmentOccurrence",
        "AttachmentScope",
        "AttachmentUpload",
        "AttachmentWriteGrant",
        "CaptureSession",
    ):
        setattr(models, name, model(name))
    for name in (
        "AttachmentImportJob",
        "AttachmentProviderConnection",
        "AttachmentProviderRecording",
        "AttachmentRecording",
        "AttachmentRecordingSegment",
        "AttachmentSourceEnvelope",
    ):
        defaults = {}
        if name == "AttachmentImportJob":
            defaults = {
                "upload_id": None,
                "recording_id": None,
                "lease_epoch": 0,
                "lease_token": None,
                "lease_until": None,
                "lease_phase": "idle",
                "state": "importing",
                "attempts": 0,
                "reservation_settled_at": None,
                "reason": "queued",
            }
        setattr(record_models, name, model(name, **defaults))
    models.AttachmentState = state
    record_models.RecordingState = state
    record_models.RecordingSegmentState = NS(
        STAGED="staged", PUBLISHED="published", ERASED="erased"
    )
    record_models.TERMINAL_RECORDING_STATES = terminal
    monkeypatch.setitem(sys.modules, "tracer.models.attachment", models)
    monkeypatch.setitem(
        sys.modules, "tracer.models.attachment_recording", record_models
    )
    bootstrap = types.ModuleType("tracer.services.attachments.bootstrap")
    bootstrap.check_session = lambda session: None
    bootstrap.session_policy = lambda session: None
    monkeypatch.setitem(sys.modules, bootstrap.__name__, bootstrap)
    module = importlib.import_module("tracer.services.attachments.imports")
    module.settings = NS(FI_ATTACHMENTS_RECORDING_LIMITS={})
    monkeypatch.setattr(module.transaction, "atomic", lambda: _atomic())
    return module


@contextmanager
def _atomic():
    yield


def lease_fixture(lane, monkeypatch):
    now = datetime.now(UTC)
    job = lane.AttachmentImportJob.objects.create(
        scope_id=uuid4(),
        recording_id=uuid4(),
        storage_binding_id=uuid4(),
        lease_epoch=1,
        lease_token=uuid4(),
        lease_until=now + timedelta(minutes=10),
        lease_phase="writing",
        deadline=now + timedelta(hours=1),
    )
    row = lane.AttachmentRecordingSegment.objects.create(
        import_job_id=job.id,
        recording_id=job.recording_id,
        scope_id=job.scope_id,
        storage_binding_id=job.storage_binding_id,
        state="staged",
        storage_version=None,
        storage_key="original/part",
        quarantine_key="quarantine/part",
        allocation_epoch=1,
        write_attempts=1,
        write_deadline_at=now + timedelta(seconds=30),
        sha256="a" * 64,
        byte_length=100,
        frame_count=28,
        cleanup_not_before=now,
        unowned_cleanup_due_at=now,
    )
    target = Row(sample_rate=16000, channels=1)
    scope, session = Row(), Row()

    @contextmanager
    def locked(*_args, **_kwargs):
        yield job, scope, session, target, None

    monkeypatch.setattr(lane, "_locked_job", locked)
    monkeypatch.setattr(lane, "_check_job", lambda *_: None)
    fence = lane.LeaseFence("import", job.id, job.lease_token, 1, job.lease_until)
    permit = lane.PutPermit(
        "segment",
        row.id,
        lane.ObjectLocator(job.storage_binding_id, "serving", row.storage_key),
        fence,
        1,
        row.write_deadline_at,
    )
    identity = lane.ObjectIdentity(
        job.storage_binding_id, "serving", row.storage_key, "version-A"
    )
    facts = lane.VerifiedAssetFacts(
        lane.StoredObject(identity, 100, "a" * 64), "audio/wav", now, 16000, 1, 28
    )
    calls = []
    storage = NS(erase_version=lambda identity: calls.append(identity))
    readiness = types.ModuleType("tracer.services.attachments.readiness")
    readiness.resolve_historical = lambda binding_id, operation: storage
    monkeypatch.setitem(sys.modules, readiness.__name__, readiness)
    return job, row, target, fence, permit, identity, facts, calls


def record(lane, job, row, permit, identity, facts):
    return lane.record_put(
        job_id=job.id,
        allocation_id=row.id,
        allocation_kind="segment",
        permit=permit,
        identity=identity,
        facts=facts,
    )


def cleanup(lane, job, row, permit, identity):
    return lane.cleanup_stale_put(
        job_id=job.id,
        allocation_id=row.id,
        allocation_kind="segment",
        permit=permit,
        identity=identity,
    )


def test_post_put_takeover_barrier_exact_cleanup_cannot_delete_B(lane, monkeypatch):
    job, row, _, _, permit_a, identity_a, facts_a, calls = lease_fixture(
        lane, monkeypatch
    )
    # Barrier A: the storage PUT returned; record_put has not run.
    job.lease_token, job.lease_epoch = uuid4(), 2
    row.allocation_epoch = 2
    row.storage_version = "version-B"
    row.state, job.state, job.lease_phase = "published", "ready", "terminal"
    before = copy.deepcopy(row.__dict__)
    # A resumes only after B's atomic publication.
    assert record(lane, job, row, permit_a, identity_a, facts_a) == "stale_unowned"
    assert cleanup(lane, job, row, permit_a, identity_a) == "stale_unowned"
    assert calls == [identity_a]
    assert {k: v for k, v in row.__dict__.items() if k != "unowned_cleanup_due_at"} == {
        k: v for k, v in before.items() if k != "unowned_cleanup_due_at"
    }


def test_recorded_A_reused_by_B_is_never_erased(lane, monkeypatch):
    job, row, _, _, permit, identity, facts, calls = lease_fixture(lane, monkeypatch)
    assert record(lane, job, row, permit, identity, facts) == "recorded"
    job.lease_token, job.lease_epoch = uuid4(), 2
    row.allocation_epoch, row.state, job.state = 2, "published", "ready"
    assert record(lane, job, row, permit, identity, facts) == "already_owned"
    assert cleanup(lane, job, row, permit, identity) == "already_owned"
    assert calls == []


@pytest.mark.parametrize("state", ["published", "erased"])
def test_late_put_cannot_reopen_allocation(lane, monkeypatch, state):
    job, row, _, _, permit, identity, facts, calls = lease_fixture(lane, monkeypatch)
    row.state = state
    job.state, job.lease_phase = "failed", "terminal"
    assert record(lane, job, row, permit, identity, facts) == "stale_unowned"
    assert cleanup(lane, job, row, permit, identity) == "stale_unowned"
    assert row.state == state and row.storage_version is None
    assert calls == [identity]


def test_current_write_is_not_eligible_for_stale_cleanup(lane, monkeypatch):
    job, row, _, _, permit, identity, _, calls = lease_fixture(lane, monkeypatch)
    with pytest.raises(lane.AttachmentError, match="conflict"):
        cleanup(lane, job, row, permit, identity)
    assert calls == []


@pytest.mark.parametrize(
    "change", ["token", "epoch", "attempt", "deadline", "binding", "key"]
)
def test_record_put_fences_every_identity_dimension(lane, monkeypatch, change):
    job, row, _, _, permit, identity, facts, calls = lease_fixture(lane, monkeypatch)
    if change == "token":
        job.lease_token = uuid4()
    elif change == "epoch":
        job.lease_epoch += 1
    elif change == "attempt":
        row.write_attempts += 1
    elif change == "deadline":
        row.write_deadline_at += timedelta(seconds=1)
    elif change == "binding":
        identity = lane.ObjectIdentity(uuid4(), "serving", row.storage_key, "version-A")
    else:
        identity = lane.ObjectIdentity(
            job.storage_binding_id, "serving", "original/other", "version-A"
        )
    if change in {"binding", "key"}:
        with pytest.raises(lane.AttachmentError):
            record(lane, job, row, permit, identity, facts)
    else:
        assert record(lane, job, row, permit, identity, facts) == "stale_unowned"
    assert row.storage_version is None and not calls


def test_failed_exact_cleanup_retains_durable_marker(lane, monkeypatch):
    job, row, _, _, permit, identity, facts, _ = lease_fixture(lane, monkeypatch)
    job.lease_epoch += 1
    readiness = sys.modules["tracer.services.attachments.readiness"]

    def fail(_identity):
        raise lane.AttachmentError("cleanup_unavailable", 503)

    readiness.resolve_historical = lambda *_args, **_kwargs: NS(erase_version=fail)
    with pytest.raises(lane.AttachmentError):
        cleanup(lane, job, row, permit, identity)
    assert row.unowned_cleanup_due_at is not None
    assert row.state == "staged" and row.storage_version is None


def test_pre_put_persists_attempt_deadline_horizon(lane, monkeypatch):
    job, row, _, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    monkeypatch.setattr(lane.sandbox, "require_import_execution", lambda *_: None)
    job.cleanup_not_before = datetime.now(UTC)
    permit = lane.prepare_put(
        job_id=job.id,
        allocation_id=row.id,
        allocation_kind="segment",
        fence=fence,
        purpose="serving",
        now=datetime.now(UTC),
    )
    assert row.write_attempts == permit.write_attempt == 2
    assert row.write_deadline_at == permit.deadline_at
    assert row.cleanup_not_before >= fence.expires_at + timedelta(seconds=15)
    row.write_attempts = 3
    with pytest.raises(lane.AttachmentError):
        lane.prepare_put(
            job_id=job.id,
            allocation_id=row.id,
            allocation_kind="segment",
            fence=fence,
            purpose="serving",
            now=datetime.now(UTC),
        )


def test_wrong_source_facts_reject_before_read(lane, monkeypatch):
    job, _, _, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    monkeypatch.setattr(lane, "_facts", lambda _: "expected")
    reader = NS(
        facts="changed", read_range=lambda **_: pytest.fail("read before facts check")
    )
    with pytest.raises(lane.AttachmentError, match="snapshot_changed"):
        lane._fetch(job, fence, reader, None)


def test_fetch_never_requests_more_than_transport_buffer(lane, monkeypatch, tmp_path):
    job, _, _, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    job.source_byte_length = 8 * 1024 * 1024 + 3
    facts, ranges = object(), []
    monkeypatch.setattr(lane, "_facts", lambda _: facts)

    def read_range(*, start, end):
        ranges.append((start, end))
        assert end - start + 1 <= 256 * 1024
        return b"x" * (end - start + 1)

    with (tmp_path / "snapshot").open("w+b") as snapshot:
        digest = lane._fetch(
            job, fence, NS(facts=facts, read_range=read_range), snapshot
        )
        assert snapshot.tell() == job.source_byte_length
        assert len(digest) == 64
    assert ranges[0][0] == 0 and ranges[-1][1] == job.source_byte_length - 1


def request(whole):
    descriptor = {
        "schema_version": 1,
        "policy_version": "1",
        "deletion_generation": 0,
        "mime_type": "audio/wav",
        "direction": "mixed",
        "source_kind": "RemoteUrl",
        "modality": "audio",
        "declared_bytes": 32044,
        "association": {
            "capture_session_id": str(uuid4()),
            "correlation_nonce": str(uuid4()),
            "parent": {"trace_id": uuid4().hex, "span_id": "1234567890abcdef"},
        },
    }
    source = {"kind": "RemoteUrlEnvelope", "envelope_id": str(uuid4())}
    if whole:
        descriptor.update(recording_id=str(uuid4()), declared_duration_ms=1000)
        return {
            "mode": "completed_recording",
            "recording": descriptor,
            "source": source,
        }
    descriptor.update(attachment_id=str(uuid4()), occurrence_id=str(uuid4()))
    return {"upload": descriptor, "source": source}


@pytest.mark.parametrize("whole", [False, True])
def test_actual_F01_import_request_and_response(lane, whole):
    body = request(whole)
    descriptor, source = lane.parse_request(body)
    assert descriptor == body["recording" if whole else "upload"]
    job = NS(
        id=uuid4(),
        upload_id=None if whole else uuid4(),
        recording=NS(client_id=uuid4()),
    )
    response = lane._response(job)
    wire = importlib.import_module("tracer.services.attachments.wire")
    wire.validate(
        "RecordingImportResponse" if whole else "SingleAttachmentImportResponse",
        response,
    )
    if not whole:
        assert set(response) == {"job_id", "upload_id", "state"}
    assert source == body["source"]


def test_single_body_never_accepts_invented_mode(lane):
    body = request(False)
    body["mode"] = "single_upload"
    with pytest.raises(lane.AttachmentError):
        lane.parse_request(body)


@pytest.mark.parametrize("whole", [False, True])
def test_atomic_admission_target_and_neutral_transfer(lane, monkeypatch, whole):
    body = request(whole)
    descriptor = body["recording" if whole else "upload"]
    scope = lane.AttachmentScope.objects.create(
        enabled=True, allow_import=True, policy_version=1, deletion_generation=0
    )
    importer = lane.Importer("user", uuid4())
    session = lane.CaptureSession.objects.create(
        scope=scope,
        client_id=descriptor["association"]["capture_session_id"],
        correlation_nonce=descriptor["association"]["correlation_nonce"],
        writer_kind="user",
        writer_id=importer.id,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    parent = descriptor["association"]["parent"]
    facts = lane.SourceFacts(
        "RemoteUrlEnvelope",
        lane.uuid.UUID(body["source"]["envelope_id"]),
        "user",
        importer.id,
        "http_strong_etag",
        '"source-v1"',
        32044,
        "audio/wav",
        1000,
        datetime.now(UTC) + timedelta(hours=1),
        0,
        lane.uuid.UUID(parent["trace_id"]),
        parent["span_id"],
        "mixed",
        None,
    )
    binding, counters, transfers = uuid4(), [], []
    monkeypatch.setattr(lane, "_admit_policy", lambda *_: None)
    monkeypatch.setattr(lane, "_lock_creator", lambda *_: None)
    monkeypatch.setattr(lane, "_lock_source", lambda *_: (None, None, None))
    # The fake ORM has no sealed-source store. Admission and replay still
    # check the fingerprint, target and association against the same facts.
    monkeypatch.setattr(lane, "_independent_replay_facts", lambda *_: facts)
    depth = NS(in_atomic_block=False)

    @contextmanager
    def atomic():
        previous, depth.in_atomic_block = depth.in_atomic_block, True
        try:
            yield
        finally:
            depth.in_atomic_block = previous

    monkeypatch.setattr(lane.transaction, "get_connection", lambda: depth)
    monkeypatch.setattr(lane.transaction, "atomic", atomic)
    source = types.ModuleType("tracer.services.attachments.source_providers")
    source._envelope_provider = lambda *_: None
    source.resolve = lambda **_: facts
    source.revalidate = lambda **_: None
    ready = types.ModuleType("tracer.services.attachments.readiness")
    ready.require_new_admission = lambda **_: binding
    validation = types.ModuleType("tracer.services.attachments.validation")
    validation.require_supported_plan = lambda mime: (
        mime == "audio/wav" or pytest.fail(mime)
    )
    validation.require_decoder = lambda req: validation.require_supported_plan(
        req.mime_type
    )
    db = types.ModuleType("tracer.services.attachments.capacity_db")
    db.count_attempt = lambda **_: True
    db.lock_capacity = lambda **_: object()
    vectors = {}

    def reserve(_locks, *, owner, amount, expires_at):
        job = lane.AttachmentImportJob.objects.get(pk=owner.id)
        assert (job.upload_id is not None) != (job.recording_id is not None)
        assert job.storage_binding_id == binding
        assert job.reservation_pool_key == "global:imports"
        vectors[owner.id] = amount
        counters.append(amount)

    def transfer(_locks, *, from_owner, to_owner, amount):
        before = vectors[from_owner.id]
        vectors[from_owner.id] = before - amount
        vectors[to_owner.id] = amount
        assert vectors[from_owner.id] + vectors[to_owner.id] == before
        transfers.append(amount)

    db.reserve_locked, db.transfer_locked = reserve, transfer
    uploads = types.ModuleType("tracer.services.attachments.uploads")

    def initialize(**kw):
        job = kw["job"]
        assert not getattr(job, "_persisted", False)
        assert job.upload_id is None and job.recording_id is None
        asset = lane.Attachment.objects.create(client_id=descriptor["attachment_id"])
        return lane.AttachmentUpload.objects.create(attachment=asset)

    uploads.initialize_import_locked = initialize
    for module in (source, ready, db, uploads, validation):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    response, created = lane.submit(importer, scope_id=scope.id, body=body)
    assert created is True
    replay, created_again = lane.submit(importer, scope_id=scope.id, body=body)
    assert replay == response and created_again is False
    assert len(counters) == 1 and len(transfers) == int(not whole)
    job = lane.AttachmentImportJob.objects.rows[0]
    assert job.capture_session_id == session.id
    assert job.deadline <= facts.expires_at and job.deadline <= session.expires_at
    assert len(lane.AttachmentImportJob.objects.rows) == 1


def test_publish_revocation_rechecked_inside_atomic_boundary(lane, monkeypatch):
    job, row, target, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    row.storage_version = "recorded"
    scope = Row()
    session = Row()
    calls = []

    @contextmanager
    def locked(*_args, **_kwargs):
        calls.append("local")
        if len(calls) > 2:
            raise lane.AttachmentError("source_unavailable", 403)
        yield job, scope, session, target, None

    monkeypatch.setattr(lane, "_locked_job", locked)
    monkeypatch.setattr(lane, "_remote_recheck", lambda *_: calls.append("remote"))
    for name in ("derivatives", "publication", "validation"):
        module = types.ModuleType("tracer.services.attachments." + name)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(lane.recordings, "validate_manifest", lambda _: None)
    with pytest.raises(lane.AttachmentError, match="source_unavailable"):
        lane.publish(job_id=job.id, fence=fence, segment_ids=(row.id,), manifest={})
    assert calls == ["local", "remote", "local"]
    assert job.state == "importing" and not lane.Attachment.objects.rows


def test_no_removed_models_or_legacy_budget_writes(lane):
    text = ROOT.joinpath("imports.py").read_text()
    assert "AttachmentBlob" not in text and "UploadReservation" not in text
    assert "IMPORT_GLOBAL_PERCENT" not in text and "_lock_budgets" not in text
    assert "erase_upload(" not in text
    assert "reserved_segments" not in text and "released_at" not in text


def test_transient_failure_does_not_reopen_idle_phase(lane, monkeypatch):
    job, _, _, fence, _, _, _, _ = lease_fixture(lane, monkeypatch)
    lane._retire(job.id, fence, reason="throttled")
    assert job.lease_phase == "writing" and job.lease_token == fence.token
    assert job.reason == "throttled"

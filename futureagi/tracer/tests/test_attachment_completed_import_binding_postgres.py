"""Own-namespace PostgreSQL qualification; not full-app or native acceptance.

Uses all literal attachment migrations, with the established five minimal
unrelated FK parents. No trigger relaxation or application database writes.
"""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from django.conf import settings
from django.db import IntegrityError, connections, transaction
from django.utils import timezone
from psycopg import sql

from tracer.tests import test_attachment_simplify_review_schema_postgres as shared
from tracer.tests.test_attachment_simplify_review_schema_postgres import (
    Case,
)

isolated_settings = shared.isolated_settings

MIGRATION = "0144_attachment_completed_import_binding"


@pytest.fixture
def binding_case(isolated_settings):
    prefix = os.environ["PG_TEST_DB_PREFIX"]
    assert prefix == "test_att_import_binding_"
    name = prefix + uuid4().hex[:12]
    admin = psycopg.connect(
        dbname=os.environ["PG_DB"],
        host=os.environ["PG_HOST"],
        port=os.environ["PG_PORT"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        autocommit=True,
    )
    assert (
        admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
        is None
    )
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    connection = connections["default"]
    connection.close()
    connection.settings_dict["NAME"] = name
    if hasattr(settings, "FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE"):
        del settings.FI_ATTACHMENTS_SCHEMA_V1_EVIDENCE
    try:
        case = Case(connection)
        case.activate()
        case.modules["0144"] = importlib.import_module("tracer.migrations." + MIGRATION)
        case.apply("0144")
        case.seed()
        yield case
    finally:
        connection.close()
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


def connection_row(case):
    return case.create(
        "AttachmentProviderConnection",
        scope=case.scope,
        provider="livekit",
        api_key_id=str(uuid4()),
        key_id="fixture",
        secret_nonce=b"0" * 12,
        secret_ciphertext=b"fixture",
        enabled=True,
    )


def receipt(case, connection, **changes):
    now = timezone.now()
    values = {
        "scope": case.scope,
        "connection": connection,
        "event_id": str(uuid4()),
        "pending_egress_id": "EG_test",
        "pending_due_at": now,
        "pending_expires_at": now + timedelta(seconds=599),
        "pending_key_id": "fixture",
        "pending_nonce": b"n" * 12,
        "pending_ciphertext": b"c" * 17,
    }
    values.update(changes)
    return case.create("AttachmentProviderEvent", **values)


def registration(case, connection, mode="auto"):
    return case.create(
        "AttachmentProviderRecording",
        scope=case.scope,
        connection=connection,
        registered_output_id=uuid4(),
        egress_id="EG_test",
        room_digest="0" * 64,
        registrar_kind="user",
        registrar_id=case.session.writer_id,
        recording_client_id=uuid4(),
        trace_id=uuid4(),
        span_id="0123456789abcdef",
        capture_session_client_id=case.session.client_id,
        correlation_nonce=case.session.correlation_nonce,
        direction="mixed",
        expires_at=timezone.now() + timedelta(hours=1),
        source_authority_generation=connection.authority_generation,
        dispatch_mode=mode,
    )


def test_pending_columns_and_blank_human_gate(binding_case):
    case = binding_case
    model = case.model("AttachmentProviderEvent")
    fields = [
        model._meta.get_field(field) for field in case.modules["0144"].PENDING_FIELDS
    ]
    assert len(fields) == 6 and all(field.null for field in fields)
    assert case.modules["0144"].UPDATE_SAFETY["reviewer"] == ""
    assert case.modules["0144"].UPDATE_SAFETY["review"] == ""
    assert (
        case.model("AttachmentProviderRecording")
        ._meta.get_field("dispatch_mode")
        .default
        == "auto"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"pending_nonce": b"n" * 11},
        {"pending_ciphertext": b"c" * 16},
        {"pending_ciphertext": b"c" * 65553},
        {"pending_key_id": None},
        {"pending_expires_at": "future"},
        {"pending_due_at": "future"},
    ],
)
def test_pending_sql_bounds(binding_case, change):
    change = {
        key: timezone.now() + timedelta(hours=1) if value == "future" else value
        for key, value in change.items()
    }
    with pytest.raises(IntegrityError):
        receipt(binding_case, connection_row(binding_case), **change)


def test_ciphertext_boundary_clear_and_no_rearm(binding_case):
    case = binding_case
    row = receipt(case, connection_row(case), pending_ciphertext=b"c" * 65552)
    values = {
        field: getattr(row, field) for field in case.modules["0144"].PENDING_FIELDS
    }
    with pytest.raises(IntegrityError):
        case.update(
            row, pending_expires_at=row.pending_expires_at + timedelta(microseconds=1)
        )
    row.refresh_from_db()
    case.update(row, **dict.fromkeys(values))
    assert row.pending_ciphertext is None
    with pytest.raises(IntegrityError):
        case.update(row, **values)
    with pytest.raises(IntegrityError):
        row.delete()
    row.refresh_from_db()
    assert row.event_id


def test_legacy_null_event_is_inert(binding_case):
    case = binding_case
    conn = connection_row(case)
    row = case.create(
        "AttachmentProviderEvent", scope=case.scope, connection=conn, event_id="legacy"
    )
    with pytest.raises(IntegrityError):
        case.update(
            row,
            pending_egress_id="EG_test",
            pending_due_at=timezone.now(),
            pending_expires_at=timezone.now() + timedelta(seconds=599),
            pending_key_id="fixture",
            pending_nonce=b"n" * 12,
            pending_ciphertext=b"c" * 17,
        )


@pytest.mark.parametrize("mode", ["auto", "sdk_explicit"])
def test_dispatch_mode_immutable(binding_case, mode):
    case = binding_case
    row = registration(case, connection_row(case), mode)
    with pytest.raises(IntegrityError):
        case.update(row, dispatch_mode="sdk_explicit" if mode == "auto" else "auto")
    row.refresh_from_db()
    assert row.dispatch_mode == mode
    if mode == "sdk_explicit":
        with pytest.raises(IntegrityError):
            case.update(row, dispatch_due_at=timezone.now())


def test_explicit_complete_has_no_dispatch_intent(binding_case):
    case = binding_case
    row = registration(case, connection_row(case), "sdk_explicit")
    now = timezone.now()
    envelope = case.create(
        "AttachmentSourceEnvelope",
        scope=case.scope,
        creator_kind="user",
        creator_id=case.session.writer_id,
        direction="mixed",
        source_bytes=10,
        key_id="fixture",
        nonce=b"0" * 12,
        ciphertext=b"fixture",
        expires_at=now + timedelta(minutes=9),
        source_expires_at=now + timedelta(minutes=9),
        authority_generation=0,
        generation_kind="http_strong_etag",
        generation='"v1"',
        mime_type="audio/wav",
        duration_ms=1,
        trace_id=row.trace_id,
        span_id=row.span_id,
    )
    case.update(
        row,
        provider_state="complete",
        output_bytes=10,
        output_duration_ms=1,
        output_envelope_id=envelope.id,
        source_generation_kind=envelope.generation_kind,
        source_generation=envelope.generation,
        source_mime_type=envelope.mime_type,
        source_expires_at=envelope.expires_at,
    )
    assert row.provider_state == "complete" and row.dispatch_due_at is None
    assert case.model("AttachmentImportJob").objects.count() == 0


def test_due_claim_survives_connection_close_and_does_not_rearm(binding_case):
    case = binding_case
    row = receipt(case, connection_row(case))
    original_expiry = row.pending_expires_at
    with transaction.atomic():
        locked = (
            case.model("AttachmentProviderEvent")
            .objects.select_for_update()
            .get(pk=row.id)
        )
        due = timezone.now() + timedelta(seconds=30)
        case.update(locked, pending_due_at=due)
    case.connection.close()  # crash after claim commit, before metadata/application
    row.refresh_from_db()
    assert row.pending_due_at == due and row.pending_expires_at == original_expiry
    assert (
        case.model("AttachmentProviderEvent")
        .objects.filter(pending_due_at__lte=due - timedelta(seconds=1))
        .count()
        == 0
    )
    assert (
        case.model("AttachmentProviderEvent")
        .objects.filter(pending_due_at__lte=due)
        .count()
        == 1
    )


def test_two_connections_33rd_ciphertext_rejected(binding_case):
    case = binding_case
    conn = connection_row(case)
    for _ in range(31):
        receipt(case, conn)
    barrier = Barrier(2)
    params = {
        "dbname": case.connection.settings_dict["NAME"],
        "host": os.environ["PG_HOST"],
        "port": os.environ["PG_PORT"],
        "user": os.environ["PG_USER"],
        "password": os.environ["PG_PASSWORD"],
    }

    def insert():
        with psycopg.connect(**params) as db:
            barrier.wait(timeout=10)
            try:
                # Production intake takes this same connection lock before cap check.
                db.execute(
                    "SELECT id FROM tracer_attachmentproviderconnection WHERE id=%s FOR UPDATE",
                    (conn.id,),
                )
                db.execute(
                    "INSERT INTO tracer_attachmentproviderevent (id,scope_id,connection_id,event_id,created_at,pending_egress_id,pending_expires_at,pending_due_at,pending_key_id,pending_nonce,pending_ciphertext) VALUES (%s,%s,%s,%s,clock_timestamp(),'EG_test',clock_timestamp()+interval '599 seconds',clock_timestamp(),'fixture',%s,%s)",
                    (
                        uuid4(),
                        case.scope.id,
                        conn.id,
                        str(uuid4()),
                        b"n" * 12,
                        b"c" * 17,
                    ),
                )
                db.commit()
                return "inserted"
            except psycopg.errors.CheckViolation:
                db.rollback()
                return "capped"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: insert(), range(2)))
    assert sorted(results) == ["capped", "inserted"]
    assert (
        case.model("AttachmentProviderEvent")
        .objects.filter(pending_ciphertext__isnull=False)
        .count()
        == 32
    )


def test_legacy_provider_job_and_alias_link_validate_identity(binding_case):
    case = binding_case
    _, job = case.import_job(provider=True, provider_file=False)
    case.update(case.provider, import_job_id=job.id, dispatch_due_at=None)
    assert case.provider.import_job_id == job.id
    with pytest.raises(IntegrityError):
        case.update(case.provider, import_job_id=uuid4())


@pytest.fixture
def services(binding_case, monkeypatch):
    """Real SQL/service/crypto/wire/capacity; synthetic identity/parent and HEAD.

    The five unrelated FK parents are still minimal. This is not qualification
    of project_writer, external parent stores, storage readiness, or decoder.
    """
    import base64
    import importlib.util
    import sys
    from pathlib import Path
    from types import ModuleType
    from types import SimpleNamespace as NS

    case = binding_case
    base = Path(__file__).resolve().parents[1] / "services" / "attachments"
    package = importlib.import_module("tracer.services.attachments")
    am = ModuleType("tracer.models.attachment")
    rm = ModuleType("tracer.models.attachment_recording")
    accounts = ModuleType("accounts.models")
    for model in case.state.apps.get_models():
        for module in (am, rm, accounts):
            setattr(module, model.__name__, model)
    am.AttachmentState = NS(
        **{
            name.upper(): name
            for name in (
                "pending_upload",
                "validating",
                "ready",
                "failed",
                "rejected",
                "expired",
                "deleted",
            )
        }
    )
    rm.ProviderRecordingState = NS(
        **{
            name.upper(): name
            for name in (
                "registered",
                "active",
                "ending",
                "complete",
                "failed",
                "aborted",
                "limit_reached",
            )
        }
    )
    rm.RecordingState = NS(
        **{
            name.upper(): name
            for name in (
                "importing",
                "validating",
                "ready",
                "failed",
                "rejected",
                "expired",
                "deleted",
            )
        }
    )
    rm.RecordingSegmentState = NS(
        STAGED="staged", PUBLISHED="published", ERASED="erased"
    )
    rm.TERMINAL_PROVIDER_STATES = {"complete", "failed", "aborted", "limit_reached"}
    rm.TERMINAL_RECORDING_STATES = {"ready", "failed", "rejected", "expired", "deleted"}
    for mod in (am, rm, accounts):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    case.model("User", "accounts").objects.create(id=case.session.writer_id)
    case.update(
        case.scope,
        allow_import=True,
        record_audio=True,
        require_media_redaction=False,
        policy_version=case.scope.policy_version + 1,
    )
    case.session = case.create(
        "CaptureSession",
        scope=case.scope,
        client_id=uuid4(),
        writer_kind="user",
        writer_id=case.session.writer_id,
        correlation_nonce=uuid4(),
        expires_at=timezone.now() + timedelta(hours=1),
        request_fingerprint="7" * 64,
        policy_version=case.scope.policy_version,
        deletion_generation=case.scope.deletion_generation,
    )
    monkeypatch.setattr(settings, "FI_ATTACHMENTS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ("example.test",), raising=False
    )
    monkeypatch.setattr(
        settings,
        "FI_ATTACHMENTS_ENVELOPE_KEYS",
        "test:" + base64.urlsafe_b64encode(b"x" * 32).decode(),
        raising=False,
    )

    def install(name, **values):
        mod = ModuleType("tracer.services.attachments." + name)
        mod.__dict__.update(values)
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
        monkeypatch.setattr(package, name, mod, raising=False)
        return mod

    contract = importlib.import_module("tracer.services.attachments.contract")

    def check_session(session):
        scope = case.model("AttachmentScope").objects.get(pk=session.scope_id)
        if (
            not scope.enabled
            or session.revoked_at
            or session.expires_at <= timezone.now()
            or session.policy_version != scope.policy_version
            or session.deletion_generation != scope.deletion_generation
            or not accounts.User.objects.filter(pk=session.writer_id).exists()
        ):
            raise contract.AttachmentError("source_unavailable", 403)
        return scope

    bootstrap = install(
        "bootstrap",
        check_session=check_session,
        session_policy=lambda _: contract.CapturePolicy(
            enabled=True, allow_import=True, record_audio=True
        ),
    )
    install("readiness", require_new_admission=lambda **_: case.binding.id)
    install(
        "validation",
        require_supported_plan=lambda _: None,
        require_admission_ready=lambda _: None,
        require_decoder=lambda _: None,
    )
    install("uploads")
    loaded = {}
    for name in (
        "envelopes",
        "provider_recordings",
        "source_providers",
        "capacity_db",
        "imports",
    ):
        qual = "tracer.services.attachments." + name
        spec = importlib.util.spec_from_file_location(qual, base / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qual, module)
        spec.loader.exec_module(module)
        monkeypatch.setattr(package, name, module, raising=False)
        loaded[name] = module
    provider = loaded["provider_recordings"]

    def parent(*args):
        assert not transaction.get_connection().in_atomic_block
        return timezone.now() + timedelta(hours=1)

    source_parent = loaded["source_providers"]._parent
    monkeypatch.setattr(provider, "_parent_expiry", parent)
    monkeypatch.setattr(loaded["source_providers"], "_parent", parent)

    def metadata(_source):
        assert not transaction.get_connection().in_atomic_block
        return '"v1"', 10, "audio/wav"

    monkeypatch.setattr(provider.safe_fetch, "inspect_metadata", metadata)

    def forbid_body(*args, **kwargs):
        raise AssertionError("no media body is permitted in control/admission tests")

    monkeypatch.setattr(provider.safe_fetch.RangeSource, "read_range", forbid_body)
    importer = loaded["imports"].Importer("user", case.session.writer_id)
    connection = provider.configure_livekit(
        scope=case.scope,
        user=None,
        api_key_id="fixture-" + uuid4().hex,
        api_secret="synthetic-provider-secret-only-for-test",
    )
    association = {
        "capture_session_id": str(case.session.client_id),
        "correlation_nonce": str(case.session.correlation_nonce),
        "parent": {"trace_id": uuid4().hex, "span_id": "1234567890abcdef"},
    }

    def register_output(mode="sdk_explicit", egress="EG_test"):
        return provider.register(
            importer,
            scope_id=case.scope.id,
            connection_id=connection.id,
            egress_id=egress,
            room_name="room-\U0001f3b5",
            recording_id=str(uuid4()),
            association=association,
            direction="mixed",
            dispatch_mode=mode,
        )

    def completion(egress="EG_test", event_id=None):
        import hashlib
        import json

        import jwt

        event = {
            "id": event_id or uuid4().hex,
            "event": "egress_ended",
            "egressInfo": {
                "egressId": egress,
                "roomName": "room-\U0001f3b5",
                "status": "EGRESS_COMPLETE",
                "updatedAt": "1",
                "fileResults": [
                    {
                        "location": "https://example.test/output?sentinel=private",
                        "size": "10",
                        "duration": "1000000",
                    }
                ],
            },
        }
        raw = json.dumps(event, separators=(",", ":")).encode()
        token = jwt.encode(
            {
                "iss": connection.api_key_id,
                "exp": int((timezone.now() + timedelta(minutes=1)).timestamp()),
                "sha256": base64.b64encode(hashlib.sha256(raw).digest()).decode(),
            },
            "synthetic-provider-secret-only-for-test",
            algorithm="HS256",
        )
        live, verified = provider.verify_livekit_webhook(raw, "Bearer " + token)
        return live, verified

    return NS(
        **loaded,
        case=case,
        importer=importer,
        connection=connection,
        association=association,
        register=register_output,
        completion=completion,
        contract=contract,
        bootstrap=bootstrap,
        source_parent=source_parent,
    )


@pytest.mark.parametrize("order", ["event_first", "registration_first"])
def test_signed_completion_rendezvous_never_admits_explicit(services, order):
    s = services
    event = s.completion()
    if order == "event_first":
        s.provider_recordings.process_livekit_event(*event)
        pending = s.case.model("AttachmentProviderEvent").objects.get()
        assert b"sentinel" not in bytes(pending.pending_ciphertext)
        assert b"example.test" not in bytes(pending.pending_ciphertext)
        row = s.register()
    else:
        row = s.register()
        s.provider_recordings.process_livekit_event(*event)
    row.refresh_from_db()
    assert row.provider_state == "complete" and row.dispatch_due_at is None
    pending = s.case.model("AttachmentProviderEvent").objects.get()
    assert pending.pending_ciphertext is None
    assert s.provider_recordings.dispatch_completed()["dispatched"] == 0
    assert s.case.model("AttachmentImportJob").objects.count() == 0
    assert s.case.model("AttachmentBudget").objects.count() == 0
    doc = s.provider_recordings.registration_document(
        row, registrar=s.importer, scope_id=s.case.scope.id
    )
    assert set(doc) == {"source", "state", "dispatch_mode", "binding", "import_request"}
    assert doc["source"] == doc["import_request"]["source"]
    assert doc["binding"]["room_digest"] == s.provider_recordings._digest(
        "room-\U0001f3b5"
    )
    inspection = s.provider_recordings.inspect_envelope(
        s.importer, scope_id=s.case.scope.id, envelope_id=row.output_envelope_id
    )
    assert inspection["registration"] == doc


def test_crash_after_receipt_and_claim_retries_without_duplicate_rearm(
    services, monkeypatch
):
    s = services
    original_try = s.provider_recordings._try_pending
    monkeypatch.setattr(s.provider_recordings, "_try_pending", lambda *args: None)
    event = s.completion()
    s.provider_recordings.process_livekit_event(*event)
    row = s.register()
    pending = s.case.model("AttachmentProviderEvent").objects.get()
    assert pending.pending_ciphertext is not None
    monkeypatch.setattr(s.provider_recordings, "_try_pending", original_try)
    inspect = s.provider_recordings._completion_metadata

    def crash(*args):
        raise RuntimeError("synthetic process boundary before metadata")

    monkeypatch.setattr(s.provider_recordings, "_completion_metadata", crash)
    assert original_try(s.case.scope.id, s.connection.id, event[1]["id"]) is None
    pending.refresh_from_db()
    frozen = (
        pending.pending_due_at,
        pending.pending_expires_at,
        bytes(pending.pending_ciphertext),
    )
    s.provider_recordings.process_livekit_event(*event)
    pending.refresh_from_db()
    assert frozen == (
        pending.pending_due_at,
        pending.pending_expires_at,
        bytes(pending.pending_ciphertext),
    )
    monkeypatch.setattr(s.provider_recordings, "_completion_metadata", inspect)
    s.provider_recordings.apply_pending_completion(
        scope_id=s.case.scope.id,
        connection_id=s.connection.id,
        event_id=pending.event_id,
        now=pending.pending_due_at,
    )
    row.refresh_from_db()
    pending.refresh_from_db()
    assert row.provider_state == "complete" and pending.pending_ciphertext is None
    s.provider_recordings.process_livekit_event(*event)
    pending.refresh_from_db()
    assert pending.pending_ciphertext is None
    assert s.case.model("AttachmentSourceEnvelope").objects.count() == 1


def _complete(s, mode="sdk_explicit"):
    row = s.register(mode=mode)
    s.provider_recordings.process_livekit_event(*s.completion())
    row.refresh_from_db()
    assert row.provider_state == "complete"
    return row, s.provider_recordings.registration_document(
        row, registrar=s.importer, scope_id=s.case.scope.id
    )["import_request"]


@pytest.mark.parametrize("mode", ["auto", "sdk_explicit"])
def test_provider_and_envelope_alias_replay_link_exact_same_job(services, mode):
    from copy import deepcopy

    s = services
    row, body = _complete(s, mode)
    first, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert created
    alias = deepcopy(body)
    alias["source"] = {
        "kind": "RemoteUrlEnvelope",
        "envelope_id": str(row.output_envelope_id),
    }
    alias["recording"]["source_kind"] = "RemoteUrl"
    alias["recording"]["association"]["parent"]["trace_id"] = str(row.trace_id)
    second, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=alias)
    assert not created and second == first
    row.refresh_from_db()
    assert str(row.import_job_id) == first["job_id"] and row.dispatch_due_at is None
    assert s.case.model("AttachmentImportJob").objects.count() == 1
    assert s.case.model("AttachmentRecording").objects.count() == 1
    assert s.provider_recordings.dispatch_completed()["dispatched"] == 0
    s.provider_recordings.revoke(
        scope_id=s.case.scope.id, connection_id=s.connection.id
    )
    with pytest.raises(s.contract.AttachmentError):
        s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert s.case.model("AttachmentImportJob").objects.count() == 1


def test_default_auto_dispatch_before_sdk_adoption_is_same_job(services):
    s = services
    row, body = _complete(s, "auto")
    assert s.provider_recordings.dispatch_completed()["dispatched"] == 1
    row.refresh_from_db()
    result, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert not created and result["job_id"] == str(row.import_job_id)
    assert s.case.model("AttachmentImportJob").objects.count() == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("declared_bytes", 11),
        ("mime_type", "audio/mpeg"),
        ("declared_duration_ms", 2),
        ("direction", "input"),
        ("recording_id", str(uuid4())),
    ],
)
def test_explicit_dispatch_first_then_bad_facts_never_reserves(services, field, value):
    s = services
    row, body = _complete(s)
    assert s.provider_recordings.dispatch_completed()["dispatched"] == 0
    body["recording"][field] = value
    with pytest.raises(s.contract.AttachmentError):
        s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert s.case.model("AttachmentImportJob").objects.count() == 0
    assert s.case.model("AttachmentBudget").objects.count() == 0


def test_two_connections_simultaneous_f01_allocation_one_job(services, monkeypatch):
    s = services
    from copy import deepcopy

    row, body = _complete(s)
    alias = deepcopy(body)
    alias["source"] = {
        "kind": "RemoteUrlEnvelope",
        "envelope_id": str(row.output_envelope_id),
    }
    alias["recording"]["source_kind"] = "RemoteUrl"
    import sys

    readiness = sys.modules["tracer.services.attachments.readiness"]
    barrier = Barrier(2)

    def ready(**kwargs):
        assert not transaction.get_connection().in_atomic_block
        barrier.wait(timeout=15)
        return s.case.binding.id

    monkeypatch.setattr(readiness, "require_new_admission", ready)

    def submit(request):
        try:
            return s.imports.submit(s.importer, scope_id=s.case.scope.id, body=request)
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (body, alias)))
    assert results[0][0] == results[1][0]
    assert sorted(value[1] for value in results) == [False, True]
    assert s.case.model("AttachmentImportJob").objects.count() == 1


def test_two_connections_crossing_registration_and_event(services, monkeypatch):
    s = services
    barrier = Barrier(2)
    parse = s.provider_recordings._pending_plaintext
    parent = s.provider_recordings._parent_expiry
    from threading import local

    thread = local()

    def before_event(*args):
        if not getattr(thread, "passed", False):
            thread.passed = True
            barrier.wait(timeout=15)
        return parse(*args)

    def before_registration(*args):
        if not getattr(thread, "passed", False):
            thread.passed = True
            barrier.wait(timeout=15)
        return parent(*args)

    monkeypatch.setattr(s.provider_recordings, "_pending_plaintext", before_event)
    monkeypatch.setattr(s.provider_recordings, "_parent_expiry", before_registration)
    event = s.completion()

    def run(operation):
        try:
            return operation()
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, s.register)
        b = pool.submit(
            run, lambda: s.provider_recordings.process_livekit_event(*event)
        )
        row = a.result(timeout=25)
        b.result(timeout=25)
    row.refresh_from_db()
    assert row.provider_state == "complete"
    assert s.case.model("AttachmentProviderEvent").objects.count() == 1
    assert s.case.model("AttachmentSourceEnvelope").objects.count() == 1
    assert s.case.model("AttachmentImportJob").objects.count() == 0


def test_wrong_registered_envelope_same_facts_cannot_alias_identity(services):
    from copy import deepcopy

    s = services
    first, body = _complete(s)
    second = s.register(egress="EG_second")
    s.provider_recordings.process_livekit_event(*s.completion(egress="EG_second"))
    second.refresh_from_db()
    alias = deepcopy(body)
    alias["source"] = {
        "kind": "RemoteUrlEnvelope",
        "envelope_id": str(second.output_envelope_id),
    }
    alias["recording"]["source_kind"] = "RemoteUrl"
    with pytest.raises(s.contract.AttachmentError):
        s.imports.submit(s.importer, scope_id=s.case.scope.id, body=alias)
    assert s.case.model("AttachmentImportJob").objects.count() == 0
    assert s.case.model("AttachmentBudget").objects.count() == 0
    doc = s.provider_recordings.inspect_envelope(
        s.importer, scope_id=s.case.scope.id, envelope_id=second.output_envelope_id
    )
    assert doc["registration"]["binding"]["egress_id"] == "EG_second"
    assert doc["registration"]["import_request"]["recording"]["recording_id"] != str(
        first.recording_client_id
    )


def test_crash_before_atomic_link_rolls_back_job_recording_and_reservation(
    services, monkeypatch
):
    s = services
    row, body = _complete(s)
    model = s.case.model("AttachmentProviderRecording")
    save = model.save

    def crash(instance, *args, **kwargs):
        if "import_job_id" in kwargs.get("update_fields", ()):
            raise RuntimeError("synthetic crash before new job link")
        return save(instance, *args, **kwargs)

    monkeypatch.setattr(model, "save", crash)
    with pytest.raises(RuntimeError):
        s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert s.case.model("AttachmentImportJob").objects.count() == 0
    assert s.case.model("AttachmentRecording").objects.count() == 0
    assert (
        not s.case.model("AttachmentBudget").objects.exclude(reserved_bytes=0).exists()
    )
    monkeypatch.setattr(model, "save", save)
    result, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    row.refresh_from_db()
    assert created and str(row.import_job_id) == result["job_id"]


def test_dispatch_recovers_exact_legacy_orphan_before_constructing_new_request(
    services, monkeypatch
):
    s = services
    row, body = _complete(s, "auto")
    model = s.case.model("AttachmentProviderRecording")
    save = model.save

    def historical_missing_link(instance, *args, **kwargs):
        # Reenact the old split transaction, not a bypass of any database guard:
        # the legacy job was committed but no link UPDATE was ever sent.
        if "import_job_id" in kwargs.get("update_fields", ()):
            return None
        return save(instance, *args, **kwargs)

    monkeypatch.setattr(model, "save", historical_missing_link)
    first, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert created
    monkeypatch.setattr(model, "save", save)
    row.refresh_from_db()
    assert row.import_job_id is None
    fingerprint = s.case.model("AttachmentImportJob").objects.get().request_fingerprint

    def forbid_new_request(*args, **kwargs):
        raise AssertionError("dispatcher must recover before rebuilding/submitting")

    monkeypatch.setattr(s.imports, "submit", forbid_new_request)
    assert s.provider_recordings.dispatch_completed()["dispatched"] == 1
    row.refresh_from_db()
    assert str(row.import_job_id) == first["job_id"]
    assert (
        s.case.model("AttachmentImportJob").objects.get().request_fingerprint
        == fingerprint
    )


def test_pending_expiry_clears_payload_but_retains_duplicate_identity(
    services, monkeypatch
):
    s = services
    monkeypatch.setattr(s.provider_recordings, "_try_pending", lambda *args: None)
    event = s.completion()
    s.provider_recordings.process_livekit_event(*event)
    receipt = s.case.model("AttachmentProviderEvent").objects.get()
    assert (
        s.provider_recordings.sweep_pending_completions(now=receipt.pending_expires_at)
        == 1
    )
    s.provider_recordings.process_livekit_event(*event)
    receipt.refresh_from_db()
    assert all(
        getattr(receipt, field) is None
        for field in s.provider_recordings._PENDING_FIELDS
    )
    assert s.case.model("AttachmentProviderEvent").objects.count() == 1
    assert s.provider_recordings.resume_pending_completions() == {
        "applied": 0,
        "deferred": 0,
    }


def test_missing_pending_key_is_retryable_not_unsigned_fallback(services, monkeypatch):
    s = services
    monkeypatch.setattr(s.provider_recordings, "_try_pending", lambda *args: None)
    row = s.register()
    event = s.completion()
    s.provider_recordings.process_livekit_event(*event)
    receipt = s.case.model("AttachmentProviderEvent").objects.get()
    keys = settings.FI_ATTACHMENTS_ENVELOPE_KEYS
    monkeypatch.setattr(settings, "FI_ATTACHMENTS_ENVELOPE_KEYS", "")
    with pytest.raises(s.contract.AttachmentError):
        s.provider_recordings.apply_pending_completion(
            scope_id=s.case.scope.id,
            connection_id=s.connection.id,
            event_id=receipt.event_id,
        )
    receipt.refresh_from_db()
    assert receipt.pending_ciphertext is not None
    row.refresh_from_db()
    assert row.provider_state == "registered"
    monkeypatch.setattr(settings, "FI_ATTACHMENTS_ENVELOPE_KEYS", keys)
    s.provider_recordings.apply_pending_completion(
        scope_id=s.case.scope.id,
        connection_id=s.connection.id,
        event_id=receipt.event_id,
        now=receipt.pending_due_at,
    )
    row.refresh_from_db()
    assert row.provider_state == "complete"


def test_revocation_during_metadata_rechecked_before_atomic_completion(
    services, monkeypatch
):
    s = services
    row = s.register()
    barrier = Barrier(2)
    inspect = s.provider_recordings.safe_fetch.inspect_metadata

    def metadata(source):
        assert not transaction.get_connection().in_atomic_block
        barrier.wait(timeout=15)
        barrier.wait(timeout=15)
        return inspect(source)

    monkeypatch.setattr(s.provider_recordings.safe_fetch, "inspect_metadata", metadata)

    def revoke():
        try:
            barrier.wait(timeout=15)
            s.provider_recordings.revoke(
                scope_id=s.case.scope.id, connection_id=s.connection.id
            )
            barrier.wait(timeout=15)
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(revoke)
        s.provider_recordings.process_livekit_event(*s.completion())
        task.result(timeout=20)
    row.refresh_from_db()
    assert row.provider_state == "registered" and row.output_envelope_id is None
    assert (
        s.case.model("AttachmentProviderEvent").objects.get().pending_ciphertext
        is not None
    )
    assert s.case.model("AttachmentSourceEnvelope").objects.count() == 0
    assert s.case.model("AttachmentImportJob").objects.count() == 0


def test_independent_envelope_inspection_keeps_null_registration(services):
    from uuid import UUID

    s = services
    parent = s.association["parent"]
    envelope_id = s.envelopes.seal(
        scope=s.case.scope,
        url="https://example.test/independent",
        creator_kind="user",
        creator_id=s.importer.id,
        direction="mixed",
        source_bytes=10,
        source_expires_at=timezone.now() + timedelta(minutes=5),
        generation_kind="http_strong_etag",
        generation='"v1"',
        mime_type="audio/wav",
        duration_ms=1,
        trace_id=UUID(parent["trace_id"]),
        span_id=parent["span_id"],
        capture_session_id=s.case.session.id,
    )
    assert s.provider_recordings.inspect_envelope(
        s.importer, scope_id=s.case.scope.id, envelope_id=envelope_id
    ) == {
        "source": {"kind": "RemoteUrlEnvelope", "envelope_id": str(envelope_id)},
        "registration": None,
    }


def test_ambiguous_reverse_ancestry_is_not_adoptable(services):
    s = services
    first, _ = _complete(s)
    second = s.register(egress="EG_ambiguous")
    s.case.update(
        second,
        provider_state="complete",
        **{
            field: getattr(first, field)
            for field in (
                "output_envelope_id",
                "output_bytes",
                "output_duration_ms",
                "source_generation_kind",
                "source_generation",
                "source_mime_type",
                "source_expires_at",
            )
        },
    )
    with pytest.raises(s.contract.AttachmentError):
        s.provider_recordings.inspect_envelope(
            s.importer, scope_id=s.case.scope.id, envelope_id=first.output_envelope_id
        )
    assert s.case.model("AttachmentImportJob").objects.count() == 0


def test_expired_trusted_source_cannot_use_historical_metadata_fallback(
    services, monkeypatch
):
    s = services
    row, body = _complete(s)
    later = row.source_expires_at + timedelta(seconds=1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    with pytest.raises(s.contract.AttachmentError):
        s.provider_recordings.registration_document(
            row, registrar=s.importer, scope_id=s.case.scope.id
        )
    with pytest.raises(s.contract.AttachmentError):
        s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert s.case.model("AttachmentImportJob").objects.count() == 0


def test_tightened_scope_policy_denies_before_explicit_f01_admission(services):
    s = services
    row, body = _complete(s)
    s.case.update(
        s.case.scope, allow_import=False, policy_version=s.case.scope.policy_version + 1
    )
    with pytest.raises(s.contract.AttachmentError):
        s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert s.provider_recordings.dispatch_completed()["dispatched"] == 0
    assert s.case.model("AttachmentImportJob").objects.count() == 0


def test_auto_dispatch_and_explicit_f01_cross_admission_barrier(services, monkeypatch):
    import sys

    s = services
    row, body = _complete(s, "auto")
    barrier = Barrier(2)

    def ready(**kwargs):
        assert not transaction.get_connection().in_atomic_block
        barrier.wait(timeout=15)
        return s.case.binding.id

    monkeypatch.setattr(
        sys.modules["tracer.services.attachments.readiness"],
        "require_new_admission",
        ready,
    )

    def call(operation):
        try:
            return operation()
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch = pool.submit(call, s.provider_recordings.dispatch_completed)
        explicit = pool.submit(
            call,
            lambda: s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body),
        )
        assert dispatch.result(timeout=25)["dispatched"] == 1
        result = explicit.result(timeout=25)[0]
    row.refresh_from_db()
    assert str(row.import_job_id) == result["job_id"]
    assert s.case.model("AttachmentImportJob").objects.count() == 1


@pytest.fixture
def accepted_independent(services, monkeypatch):
    """Real replay/crypto/SQL and _parent; synthetic external authority results.

    No copied authorization rules: the production _parent decides whether the
    supplied authority result grants access. This is not LiveAuthority/CH proof.
    """
    import sys
    from collections import namedtuple
    from copy import deepcopy
    from types import ModuleType
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from uuid import UUID

    s = services
    now = timezone.now()
    state = NS(value=NS(readable=True, expires_at=now + timedelta(hours=1)))

    def parent_lookup(viewer, scope, parent):
        assert not transaction.get_connection().in_atomic_block
        assert viewer.user_id == s.importer.id and scope.id == s.case.scope.id
        assert parent.trace_id == UUID(s.association["parent"]["trace_id"])
        assert parent.span_id == s.association["parent"]["span_id"]
        return state.value

    parent_source = Mock(side_effect=parent_lookup)
    authority = ModuleType("tracer.services.attachments.authority")
    authority.LiveAuthority = Mock(return_value=NS(source=parent_source))
    # Only a value type is substituted to avoid importing unrelated app models.
    occurrences = ModuleType("tracer.services.attachments.occurrences")
    occurrences.Parent = namedtuple("Parent", "trace_id span_id")
    monkeypatch.setitem(sys.modules, authority.__name__, authority)
    monkeypatch.setitem(sys.modules, occurrences.__name__, occurrences)
    monkeypatch.setattr(s.source_providers, "_parent", s.source_parent)

    _, provider_body = _complete(s)
    parent = s.association["parent"]
    expiry = now + timedelta(minutes=2)
    envelope_id = s.envelopes.seal(
        scope=s.case.scope,
        url="https://example.test/independent",
        creator_kind="user",
        creator_id=s.importer.id,
        direction="mixed",
        source_bytes=10,
        source_expires_at=expiry,
        generation_kind="http_strong_etag",
        generation='"v1"',
        mime_type="audio/wav",
        duration_ms=1,
        trace_id=UUID(parent["trace_id"]),
        span_id=parent["span_id"],
        capture_session_id=s.case.session.id,
    )
    body = deepcopy(provider_body)
    body["source"] = {"kind": "RemoteUrlEnvelope", "envelope_id": str(envelope_id)}
    body["recording"]["source_kind"] = "RemoteUrl"
    body["recording"]["recording_id"] = str(uuid4())
    first, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert created
    job = s.case.model("AttachmentImportJob").objects.get(pk=first["job_id"])
    envelope = s.case.model("AttachmentSourceEnvelope").objects.get(pk=envelope_id)
    parent_source.reset_mock()

    def snapshot():
        # Usage receipts are inline on Upload, not a separate receipt model.
        # Keep every receipt column in the full-row snapshot, plus its audit/outbox.
        receipt_fields = {
            "usage_id",
            "usage_created_at",
            "usage_outcome",
            "usage_stored_bytes",
            "settlement_event_id",
        }
        assert receipt_fields <= {
            field.name for field in s.case.model("AttachmentUpload")._meta.fields
        }
        return {
            name: list(s.case.model(name).objects.order_by("pk").values())
            for name in (
                "AttachmentImportJob",
                "AttachmentRecording",
                "AttachmentBudget",
                "Attachment",
                "AttachmentUpload",
                "AttachmentRecordingSegment",
                "AttachmentOccurrence",
                "AttachmentManifestRevision",
                "AttachmentAuditEvent",
                "AttachmentOutbox",
            )
        }

    before = snapshot()
    forbidden = []
    for module, names in (
        (s.source_providers, ("open_source", "SourceReader")),
        (s.source_providers.safe_fetch, ("inspect_metadata",)),
        (s.source_providers.safe_fetch.RangeSource, ("read_range",)),
        (s.envelopes, ("open_url",)),
        (
            s.capacity_db,
            ("lock_capacity", "count_attempt", "reserve_locked", "transfer_locked"),
        ),
        (s.imports, ("publish", "run")),
        (
            sys.modules["tracer.services.attachments.readiness"],
            ("require_new_admission",),
        ),
        (
            sys.modules["tracer.services.attachments.validation"],
            ("require_admission_ready", "require_decoder"),
        ),
        (s.case.model("AttachmentImportJob"), ("save",)),
        (s.case.model("AttachmentRecording"), ("save",)),
    ):
        for name in names:
            spy = Mock(side_effect=AssertionError("replay must not perform " + name))
            monkeypatch.setattr(module, name, spy)
            forbidden.append(spy)
    yield NS(
        s=s,
        body=body,
        first=first,
        job=job,
        envelope=envelope,
        expiry=expiry,
        state=state,
        parent_source=parent_source,
        parent_lookup=parent_lookup,
    )
    for spy in forbidden:
        spy.assert_not_called()
    assert (
        snapshot() == before
    )  # includes job/fingerprint, allocations and publication receipts


def _accepted_replay(case):
    return case.s.imports.submit(
        case.s.importer, scope_id=case.s.case.scope.id, body=case.body
    )


def test_independent_envelope_accepted_replay_keeps_legacy_expiry_behavior(
    accepted_independent, monkeypatch
):
    c = accepted_independent
    # Natural expiry, not a mutation of the immutable admitted facts. The old
    # test's response identity promise remains, now with real parent validation.
    monkeypatch.setattr(timezone, "now", lambda: c.expiry + timedelta(seconds=1))
    replay, created = _accepted_replay(c)
    assert not created and replay == c.first
    c.parent_source.assert_called_once()


def test_independent_authorized_replay_keeps_identity_and_fingerprint(
    accepted_independent,
):
    c = accepted_independent
    fingerprint = c.job.request_fingerprint
    assert _accepted_replay(c) == (c.first, False)
    assert _accepted_replay(c) == (c.first, False)
    c.job.refresh_from_db()
    assert c.job.request_fingerprint == fingerprint
    assert c.parent_source.call_count == 2


@pytest.mark.parametrize("change", ["revoked", "generation", "creator", "missing"])
@pytest.mark.parametrize("expired", [False, True])
def test_independent_replay_requires_current_envelope_authority(
    accepted_independent, monkeypatch, change, expired
):
    c = accepted_independent
    if expired:
        monkeypatch.setattr(timezone, "now", lambda: c.expiry + timedelta(seconds=1))
    if change == "missing":
        c.s.envelopes.destroy(scope_id=c.s.case.scope.id, envelope_id=c.envelope.id)
    elif change == "creator":
        # Creator identity cannot change in SQL. Prove that guard, then exercise
        # the service's inconsistent-snapshot defense without relaxing it.
        creator_id = uuid4()
        with pytest.raises(IntegrityError, match="attachment identity immutable"):
            c.s.case.update(c.envelope, creator_id=creator_id)
        model = c.s.case.model("AttachmentSourceEnvelope")
        from_db = model.from_db

        def changed(cls, db, fields, values):
            row = from_db(db, fields, values)
            if row.pk == c.envelope.id:
                row.creator_id = creator_id
            return row

        monkeypatch.setattr(model, "from_db", classmethod(changed))
    else:
        changes = {
            "revoked": {"revoked_at": timezone.now(), "authority_generation": 1},
            "generation": {"authority_generation": 1},
        }[change]
        c.s.case.update(c.envelope, **changes)
    with pytest.raises(c.s.contract.AttachmentError):
        _accepted_replay(c)


@pytest.mark.parametrize("parent", ["denied", "unknown", "expired", "shortened"])
@pytest.mark.parametrize("expired", [False, True])
def test_independent_replay_requires_current_parent_authority(
    accepted_independent, monkeypatch, parent, expired
):
    c = accepted_independent
    if expired:
        monkeypatch.setattr(timezone, "now", lambda: c.expiry + timedelta(seconds=1))
    if parent == "unknown":
        c.state.value = None
    elif parent == "denied":
        c.state.value.readable = False
    elif parent == "expired":
        c.state.value.expires_at = timezone.now()
    else:
        c.state.value.expires_at = c.expiry - timedelta(seconds=1)
    with pytest.raises(c.s.contract.AttachmentError) as error:
        _accepted_replay(c)
    assert error.value.status == (503 if parent == "unknown" else 403)
    c.parent_source.assert_called_once()


@pytest.mark.parametrize(
    "field", ["importer_id", "capture_session_id", "envelope_id", "source_creator_id"]
)
@pytest.mark.parametrize("locked_only", [False, True])
def test_independent_replay_rejects_inconsistent_job_snapshot(
    accepted_independent, monkeypatch, field, locked_only
):
    c = accepted_independent
    model = c.s.case.model("AttachmentImportJob")
    from_db = model.from_db

    def changed(cls, db, fields, values):
        row = from_db(db, fields, values)
        if row.pk == c.job.id and (
            not locked_only or transaction.get_connection().in_atomic_block
        ):
            setattr(row, field, uuid4())
        return row

    # SQL identity guards are not disabled: inject an inconsistent ORM snapshot
    # to exercise the service's exact association checks independently of them.
    monkeypatch.setattr(model, "from_db", classmethod(changed))
    with pytest.raises(c.s.contract.AttachmentError):
        _accepted_replay(c)


@pytest.mark.parametrize(
    "field", ["client_id", "capture_session_id", "correlation_nonce", "trace_id"]
)
def test_independent_replay_rejects_inconsistent_recording_association(
    accepted_independent, monkeypatch, field
):
    c = accepted_independent
    model = c.s.case.model("AttachmentRecording")
    from_db = model.from_db

    def changed(cls, db, fields, values):
        row = from_db(db, fields, values)
        if row.pk == c.job.recording_id:
            setattr(row, field, uuid4())
        return row

    monkeypatch.setattr(model, "from_db", classmethod(changed))
    with pytest.raises(c.s.contract.AttachmentError):
        _accepted_replay(c)
    c.parent_source.assert_called_once()


@pytest.mark.parametrize("revoked", [False, True])
def test_independent_replay_rechecks_late_source_change_under_locks(
    accepted_independent, revoked
):
    c = accepted_independent

    def change_after_parent(*args):
        result = c.parent_lookup(*args)
        changes = {"authority_generation": 1}
        if revoked:
            changes["revoked_at"] = timezone.now()
        c.s.case.update(c.envelope, **changes)
        return result

    c.parent_source.side_effect = change_after_parent
    with pytest.raises(c.s.contract.AttachmentError):
        _accepted_replay(c)
    c.parent_source.assert_called_once()  # no network/parent lookup under locks


def test_independent_replay_requires_retained_capture_proof(
    accepted_independent, monkeypatch
):
    c = accepted_independent
    monkeypatch.setattr(settings, "FI_ATTACHMENTS_ENVELOPE_KEYS", "")
    with pytest.raises(c.s.contract.AttachmentError):
        _accepted_replay(c)


def test_independent_replay_preserves_fingerprint_conflict(accepted_independent):
    c = accepted_independent
    c.body["recording"]["declared_bytes"] += 1
    with pytest.raises(c.s.contract.AttachmentError) as error:
        _accepted_replay(c)
    assert error.value.code == "idempotency_conflict"
    c.parent_source.assert_not_called()


def test_expired_accepted_metadata_does_not_enable_resolve_or_new_admission(
    accepted_independent, monkeypatch
):
    from copy import deepcopy

    c = accepted_independent
    monkeypatch.setattr(timezone, "now", lambda: c.expiry + timedelta(seconds=1))
    assert _accepted_replay(c) == (c.first, False)
    with pytest.raises(c.s.contract.AttachmentError):
        c.s.source_providers.resolve(
            source=c.s.imports.SourceRef("RemoteUrlEnvelope", c.envelope.id),
            scope_id=c.s.case.scope.id,
            importer=c.s.importer,
            capture_session_id=c.s.case.session.id,
        )
    new_body = deepcopy(c.body)
    new_body["recording"]["recording_id"] = str(uuid4())
    with pytest.raises(c.s.contract.AttachmentError):
        c.s.imports.submit(c.s.importer, scope_id=c.s.case.scope.id, body=new_body)


def test_independent_replay_takes_only_ordered_local_locks(accepted_independent):
    c = accepted_independent
    tables = (
        "tracer_attachmentscope",
        "accounts_user",
        "tracer_capturesession",
        "tracer_attachmentsourceenvelope",
        "tracer_attachmentimportjob",
        "tracer_attachmentrecording",
    )
    observed = []

    def observe(execute, sql, params, many, context):
        if "FOR UPDATE" in sql.upper():
            matched = [name for name in tables if '"' + name + '"' in sql]
            assert len(matched) == 1, sql
            observed.append(matched[0])
        return execute(sql, params, many, context)

    with c.s.case.connection.execute_wrapper(observe):
        assert _accepted_replay(c) == (c.first, False)
    assert observed == list(tables)
    c.parent_source.assert_called_once()


def test_registered_accepted_aliases_keep_strict_expiry(services, monkeypatch):
    from copy import deepcopy
    from unittest.mock import Mock

    s = services
    row, body = _complete(s)
    first, created = s.imports.submit(s.importer, scope_id=s.case.scope.id, body=body)
    assert created
    alias = deepcopy(body)
    alias["source"] = {
        "kind": "RemoteUrlEnvelope",
        "envelope_id": str(row.output_envelope_id),
    }
    alias["recording"]["source_kind"] = "RemoteUrl"
    before = list(s.case.model("AttachmentBudget").objects.order_by("pk").values())
    independent = Mock(side_effect=AssertionError("registered aliases stay strict"))
    monkeypatch.setattr(s.imports, "_independent_replay", independent)
    later = row.source_expires_at + timedelta(seconds=1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    for request in (body, alias):
        with pytest.raises(s.contract.AttachmentError):
            s.imports.submit(s.importer, scope_id=s.case.scope.id, body=request)
    independent.assert_not_called()
    assert (
        list(s.case.model("AttachmentBudget").objects.order_by("pk").values()) == before
    )
    assert str(s.case.model("AttachmentImportJob").objects.get().id) == first["job_id"]

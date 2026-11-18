"""Exact source ancestry and retry control flow; no network or database."""

import sys
from datetime import timedelta
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tracer.tests import test_attachment_simplify_sources_control as shared
from tracer.tests.test_attachment_simplify_sources_control import (
    NOW,
    Query,
    row,
    setup_source,
)

modules = shared.modules


def ancestry(m, monkeypatch):
    facts, scope, session, envelope, _ = setup_source(m, monkeypatch)
    connection = row(
        id=uuid4(),
        scope_id=scope.id,
        provider="livekit",
        enabled=True,
        revoked_at=None,
        authority_generation=0,
    )
    provider = row(
        id=uuid4(),
        scope_id=scope.id,
        connection_id=connection.id,
        output_envelope_id=envelope.id,
        provider_state="complete",
        expires_at=facts.expires_at,
        source_expires_at=facts.expires_at,
        registrar_kind=facts.creator_kind,
        registrar_id=facts.creator_id,
        source_authority_generation=0,
        capture_session_client_id=session.client_id,
        correlation_nonce=session.correlation_nonce,
        source_generation_kind=facts.generation_kind,
        source_generation=facts.generation,
        output_bytes=facts.byte_length,
        source_mime_type=facts.mime_type,
        output_duration_ms=facts.duration_ms,
        trace_id=facts.parent_trace_id,
        span_id=facts.parent_span_id,
        direction=facts.direction,
    )
    m.rm.AttachmentProviderRecording.objects = Query([provider])
    m.rm.AttachmentProviderConnection.objects = Query([connection])
    return facts, scope, session, envelope, connection, provider


@pytest.mark.parametrize("kind", ["RemoteUrlEnvelope", "ProviderFile"])
def test_connection_envelope_registration_order_and_revocation_recheck(
    modules, monkeypatch, kind
):
    m = modules
    facts, scope, session, envelope, connection, provider = ancestry(m, monkeypatch)
    events = []
    for model, label in (
        (m.rm.AttachmentProviderConnection, "connection"),
        (m.rm.AttachmentSourceEnvelope, "envelope"),
        (m.rm.AttachmentProviderRecording, "registration"),
    ):
        query = model.objects

        def lock(query=query, label=label, **kwargs):
            events.append(label)
            return query

        monkeypatch.setattr(query, "select_for_update", lock)
    source = m.interfaces.SourceRef(
        kind, envelope.id if kind == "RemoteUrlEnvelope" else provider.id
    )
    with m.provider_recordings.transaction.atomic():
        rows = m.source_providers.lock_source(scope_id=scope.id, source=source)
        assert rows == (connection, envelope, provider)
        assert events == ["connection", "envelope", "registration"]
        # Revalidation must never request another connection lock after Job.
        connection.enabled = False
        with pytest.raises(m.contract.AttachmentError):
            m.source_providers.revalidate(
                facts=facts,
                scope_id=scope.id,
                importer=NS(kind=facts.creator_kind, id=facts.creator_id),
                capture_session_id=session.id,
            )
        assert events == ["connection", "envelope", "registration"]


def test_ancestry_change_while_waiting_does_not_acquire_late_connection(
    modules, monkeypatch
):
    m = modules
    facts, scope, session, envelope, connection, provider = ancestry(m, monkeypatch)
    # Use distinct unlocked snapshot and locked row, as the ORM does.
    before = NS(**provider.__dict__)
    monkeypatch.setattr(m.source_providers, "_envelope_provider", lambda *args: before)
    provider.connection_id = uuid4()
    with (
        m.provider_recordings.transaction.atomic(),
        pytest.raises(m.contract.AttachmentError) as error,
    ):
        m.source_providers.lock_source(
            scope_id=scope.id,
            source=m.interfaces.SourceRef("RemoteUrlEnvelope", envelope.id),
        )
    assert error.value.code == "conflict"


def test_ambiguous_provider_ancestry_fails_closed(modules, monkeypatch):
    m = modules
    _, scope, _, envelope, _, provider = ancestry(m, monkeypatch)
    m.rm.AttachmentProviderRecording.objects.rows.append(
        NS(**{**provider.__dict__, "id": uuid4()})
    )
    with (
        m.provider_recordings.transaction.atomic(),
        pytest.raises(m.contract.AttachmentError),
    ):
        m.source_providers.lock_source(
            scope_id=scope.id,
            source=m.interfaces.SourceRef("RemoteUrlEnvelope", envelope.id),
        )


def test_envelope_and_provider_authority_generations_are_independent(
    modules, monkeypatch
):
    m = modules
    facts, scope, session, envelope, connection, provider = ancestry(m, monkeypatch)
    connection.authority_generation = provider.source_authority_generation = 9
    # The admitted envelope generation stays 0; provider generation is a separate
    # authority fence, not an invented envelope.connection_id field.
    m.source_providers.revalidate(
        facts=facts,
        scope_id=scope.id,
        importer=NS(kind=facts.creator_kind, id=facts.creator_id),
        capture_session_id=session.id,
    )
    connection.authority_generation = 10
    with pytest.raises(m.contract.AttachmentError):
        m.source_providers.revalidate(
            facts=facts,
            scope_id=scope.id,
            importer=NS(kind=facts.creator_kind, id=facts.creator_id),
            capture_session_id=session.id,
        )


def test_unknown_parent_is_retryable_but_still_denied(modules, monkeypatch):
    m = modules
    facts = m.interfaces.SourceFacts(
        "RemoteUrlEnvelope",
        uuid4(),
        "user",
        uuid4(),
        "http_strong_etag",
        '"v1"',
        8,
        "audio/wav",
        1,
        NOW + timedelta(minutes=5),
        0,
        uuid4(),
        "1234567890abcdef",
        "mixed",
        None,
    )
    authority = ModuleType("tracer.services.attachments.authority")
    authority.LiveAuthority = lambda: NS(source=lambda *args: None)
    occurrences = ModuleType("tracer.services.attachments.occurrences")
    occurrences.Parent = lambda *args: NS()
    monkeypatch.setitem(sys.modules, authority.__name__, authority)
    monkeypatch.setitem(sys.modules, occurrences.__name__, occurrences)
    with pytest.raises(m.contract.AttachmentError) as error:
        m.source_providers._parent(facts, NS(), NS(id=facts.creator_id, kind="user"))
    assert (error.value.code, error.value.status) == ("source_unavailable", 503)


def dispatch_setup(m, monkeypatch, code, status):
    registrar = NS(kind="user", id=uuid4())
    scope_id, job_id = uuid4(), uuid4()
    session = row(
        id=uuid4(),
        scope_id=scope_id,
        client_id=uuid4(),
        correlation_nonce=uuid4(),
        writer_kind=registrar.kind,
        writer_id=registrar.id,
    )
    registration = row(
        id=uuid4(),
        scope_id=scope_id,
        provider_state="complete",
        import_job_id=None,
        dispatch_due_at=NOW,
        dispatch_attempts=0,
        expires_at=NOW + timedelta(minutes=5),
        source_expires_at=NOW + timedelta(seconds=11),
        registrar_kind=registrar.kind,
        registrar_id=registrar.id,
        capture_session_client_id=session.client_id,
        correlation_nonce=session.correlation_nonce,
    )
    m.rm.AttachmentProviderRecording.objects = Query([registration])
    m.am.CaptureSession.objects = Query([session])
    m.rm.AttachmentImportJob.objects = Query(
        [
            row(
                id=job_id,
                scope_id=scope_id,
                provider_recording_id=registration.id,
                importer_kind=registrar.kind,
                importer_id=registrar.id,
            )
        ]
    )
    imports = ModuleType("tracer.services.attachments.imports")
    imports.Importer = lambda kind, ident: NS(kind=kind, id=ident)
    attempts = []

    def submit(*args, **kwargs):
        assert m.depth.value == 0
        attempts.append(True)
        if len(attempts) == 1:
            raise m.contract.AttachmentError(code, status)
        return {"job_id": str(job_id)}, False

    imports.submit = submit
    monkeypatch.setitem(sys.modules, imports.__name__, imports)
    monkeypatch.setattr(
        sys.modules["tracer.services.attachments"], "imports", imports, raising=False
    )
    monkeypatch.setattr(m.provider_recordings, "_dispatch_body", lambda *args: {})
    return registration, attempts, imports


@pytest.mark.parametrize(
    "code,status",
    [
        ("source_unavailable", 403),
        ("source_unavailable", 503),
        ("throttled", 429),
        ("conflict", 409),
    ],
)
def test_dispatch_unknown_authority_retains_retry_and_recovers(
    modules, monkeypatch, code, status
):
    registration, attempts, _ = dispatch_setup(modules, monkeypatch, code, status)
    assert modules.provider_recordings.dispatch_completed(now=NOW)["deferred"] == 1
    assert NOW < registration.dispatch_due_at <= registration.source_expires_at
    assert (
        modules.provider_recordings.dispatch_completed(
            now=registration.dispatch_due_at
        )["dispatched"]
        == 1
    )
    assert len(attempts) == 2 and registration.dispatch_due_at is None


def test_dispatch_retries_stop_at_source_deadline(modules, monkeypatch):
    registration, _, imports = dispatch_setup(
        modules, monkeypatch, "source_unavailable", 403
    )
    imports.submit = Mock(
        side_effect=modules.contract.AttachmentError("source_unavailable", 403)
    )
    assert modules.provider_recordings.dispatch_completed(now=NOW)["deferred"] == 1
    assert (
        modules.provider_recordings.dispatch_completed(
            now=registration.dispatch_due_at
        )["deferred"]
        == 1
    )
    assert registration.dispatch_due_at == registration.source_expires_at
    assert (
        modules.provider_recordings.dispatch_completed(
            now=registration.dispatch_due_at
        )["denied"]
        == 1
    )
    assert registration.dispatch_due_at is None and imports.submit.call_count == 2


@pytest.mark.parametrize(
    "code", ["policy_denied", "permission_denied", "deleted", "snapshot_changed"]
)
def test_dispatch_proven_terminal_denial_clears_cursor(modules, monkeypatch, code):
    registration, attempts, _ = dispatch_setup(modules, monkeypatch, code, 403)
    assert modules.provider_recordings.dispatch_completed(now=NOW)["denied"] == 1
    assert registration.dispatch_due_at is None and len(attempts) == 1


def test_missing_sealed_material_does_not_block_terminal_locking(modules, monkeypatch):
    m = modules
    facts, scope, session, envelope, connection, provider = ancestry(m, monkeypatch)
    m.rm.AttachmentSourceEnvelope.objects = Query([])
    with m.provider_recordings.transaction.atomic():
        assert m.source_providers.lock_source(
            scope_id=scope.id,
            source=m.interfaces.SourceRef("RemoteUrlEnvelope", envelope.id),
        ) == (connection, None, provider)
        # No source authorization is conferred by cleanup's lock helper.
        with pytest.raises(m.contract.AttachmentError):
            m.source_providers.revalidate(
                facts=facts,
                scope_id=scope.id,
                importer=NS(kind=facts.creator_kind, id=facts.creator_id),
                capture_session_id=session.id,
            )

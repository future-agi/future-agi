"""Durable observation of COMPLETED provider recordings (B08, LiveKit Egress).

What this module never does: start, stop or list Egress; accept provider
credentials or a connection choice from an upload capability; trust an
unsigned or replayed webhook; or treat "started/stopped" as completion.

* A scope owner explicitly configures a provider connection. Its API secret is
  sealed with the attachment envelope key and only used to verify webhooks and
  (when qualified) for fixed-ID status reconciliation.
* An eligible original identity REGISTERS an existing egress by its fixed ID,
  room and recording association. Registration is not a recording opt-in and
  never creates billable work.
* Verified webhooks are fenced per (connection, event id), matched to the exact
  registration (connection + egress ID + room digest) and applied
  monotonically: terminal provider states are sticky and older updates never
  regress newer ones.
* A completed registration enqueues exactly one completed-recording import,
  under the registrar's identity and all ordinary import admission rules.

Signed completed outputs are sealed after metadata-only inspection. Source
bytes use source_providers.open_source only after durable capacity admission;
completion dispatch has its own persisted retry cursor, independent of replay.
"""

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import timedelta
from types import SimpleNamespace

import jwt
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from tracer.models.attachment import AttachmentScope, CaptureSession
from tracer.models.attachment_recording import (
    TERMINAL_PROVIDER_STATES,
    AttachmentProviderConnection,
    AttachmentProviderEvent,
    AttachmentProviderRecording,
    ProviderRecordingState,
)
from tracer.services.attachments import bootstrap, envelopes, safe_fetch
from tracer.services.attachments.contract import AttachmentError, span_id, trace_uuid

WEBHOOK_MAX_BYTES = 64 * 1024
REGISTRATION_HOURS = 24
MAX_PENDING_PER_CONNECTION = 32
PENDING_TTL_SECONDS = min(envelopes.ENVELOPE_SECONDS, REGISTRATION_HOURS * 3600)
PENDING_RETRY_SECONDS = 30
_PENDING_FIELDS = (
    "pending_egress_id",
    "pending_expires_at",
    "pending_due_at",
    "pending_key_id",
    "pending_nonce",
    "pending_ciphertext",
)
LIVEKIT_STATES = {
    "EGRESS_STARTING": ProviderRecordingState.REGISTERED,
    "EGRESS_ACTIVE": ProviderRecordingState.ACTIVE,
    "EGRESS_ENDING": ProviderRecordingState.ENDING,
    "EGRESS_COMPLETE": ProviderRecordingState.COMPLETE,
    "EGRESS_FAILED": ProviderRecordingState.FAILED,
    "EGRESS_ABORTED": ProviderRecordingState.ABORTED,
    "EGRESS_LIMIT_REACHED": ProviderRecordingState.LIMIT_REACHED,
}
_LIVEKIT_NUMERIC = dict(enumerate(LIVEKIT_STATES))
_RANK = {
    ProviderRecordingState.REGISTERED: 0,
    ProviderRecordingState.ACTIVE: 1,
    ProviderRecordingState.ENDING: 2,
}


def _open_livekit_source(*, job_id, fence):
    from tracer.services.attachments.source_providers import open_source

    return open_source(job_id=job_id, fence=fence)


# Concrete, server-owned implementation. No registration can supply a factory.
READERS = {"livekit": _open_livekit_source}


def _aad(scope_id, connection_id):
    return f"fi.attachments.v1.connection:{scope_id}:{connection_id}".encode()


def _digest(value):
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except (UnicodeError, AttributeError):
        raise AttachmentError("validation_failed", 422) from None


def configure_livekit(*, scope, user, api_key_id, api_secret):
    """Owner action: explicit connection; disabled connections verify nothing."""
    if (
        not isinstance(api_key_id, str)
        or not 1 <= len(api_key_id) <= 64
        or not api_key_id.isprintable()
        or not isinstance(api_secret, str)
        or not 16 <= len(api_secret) <= 256
    ):
        raise AttachmentError("validation_failed", 422)
    connection_id = uuid.uuid4()
    key_id, nonce, ciphertext = envelopes.seal_bytes(
        api_secret.encode(), _aad(scope.id, connection_id)
    )
    try:
        return AttachmentProviderConnection.objects.create(
            id=connection_id,
            scope=scope,
            provider="livekit",
            api_key_id=api_key_id,
            key_id=key_id,
            secret_nonce=nonce,
            secret_ciphertext=ciphertext,
            created_by=user,
            enabled=True,
        )
    except IntegrityError:
        raise AttachmentError("conflict", 409) from None


def revoke(*, scope_id, connection_id):
    return AttachmentProviderConnection.objects.filter(
        pk=connection_id, scope_id=scope_id, revoked_at__isnull=True
    ).update(
        enabled=False,
        revoked_at=timezone.now(),
        authority_generation=F("authority_generation") + 1,
    )


def _live_connection(connection_id, scope_id=None):
    query = AttachmentProviderConnection.objects.filter(
        pk=connection_id, enabled=True, revoked_at__isnull=True
    )
    if scope_id is not None:
        query = query.filter(scope_id=scope_id)
    connection = query.first()
    if connection is None:
        raise AttachmentError("source_unavailable", 403)
    scope = AttachmentScope.objects.get(pk=connection.scope_id)
    if not scope.enabled:
        raise AttachmentError("disabled", 403)
    return connection


def _registrar_session(registrar, scope_id, association):
    """The registrar must own the live capture session it names, at register time.

    Checking only when the import is enqueued would let anyone holding an
    egress id park a registration against another writer's session.
    """
    from tracer.services.attachments.bootstrap import check_session

    try:
        client_id = uuid.UUID(association["capture_session_id"])
        nonce = uuid.UUID(association["correlation_nonce"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AttachmentError("validation_failed", 422) from None
    session = CaptureSession.objects.filter(
        scope_id=scope_id,
        client_id=client_id,
        correlation_nonce=nonce,
        writer_kind=registrar.kind,
        writer_id=registrar.id,
    ).first()
    if session is None:
        raise AttachmentError("not_found", 404)
    check_session(session)
    return session


def register(
    registrar,
    *,
    scope_id,
    connection_id,
    egress_id,
    room_name,
    recording_id,
    association,
    direction,
    dispatch_mode="auto",
):
    """Register an EXISTING egress by fixed ID. Never starts or lists Egress.

    ``registrar`` is an authenticated original identity; a write-only upload
    capability cannot reach this function or select a connection.
    """
    if (
        not isinstance(egress_id, str)
        or not re.fullmatch(r"EG_[A-Za-z0-9]{1,61}", egress_id)
        or not isinstance(room_name, str)
        or not 1 <= len(room_name) <= 256
        or not isinstance(direction, str)
        or direction not in {"input", "output", "mixed"}
        or not isinstance(dispatch_mode, str)
        or dispatch_mode not in {"auto", "sdk_explicit"}
    ):
        raise AttachmentError("validation_failed", 422)
    from tracer.services.attachments import wire
    from tracer.services.attachments.contract import admit_audio

    wire.validate("Association", association)
    wire.validate("UUID", str(recording_id))
    parent = association["parent"]
    trace_id, parent_span = trace_uuid(parent["trace_id"]), span_id(parent["span_id"])
    session = _registrar_session(registrar, scope_id, association)
    scope = AttachmentScope.objects.get(pk=scope_id)
    retention = _parent_expiry(scope, registrar, trace_id, parent_span)
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        session = _lock_registrar(registrar, scope_id, session.id)
        connection = (
            AttachmentProviderConnection.objects.select_for_update()
            .filter(pk=connection_id, scope_id=scope_id)
            .first()
        )
        if connection is None:
            raise AttachmentError("not_found", 404)
        _check_connection(connection)
        bootstrap.check_session(session)
        policy = bootstrap.session_policy(session)
        if not scope.allow_import or not policy.allow_import:
            raise AttachmentError("source_unavailable", 403)
        admit_audio(
            scope, policy, direction=direction, source_kind="imported_recording"
        )
        identity = {
            "scope_id": scope_id,
            "connection_id": connection.id,
            "egress_id": egress_id,
            "room_digest": _digest(room_name),
            "registrar_kind": registrar.kind,
            "registrar_id": registrar.id,
            "recording_client_id": uuid.UUID(str(recording_id)),
            "trace_id": trace_id,
            "span_id": parent_span,
            "capture_session_client_id": session.client_id,
            "correlation_nonce": session.correlation_nonce,
            "direction": direction,
            "dispatch_mode": dispatch_mode,
        }
        existing = AttachmentProviderRecording.objects.filter(
            connection=connection, egress_id=egress_id
        ).first()
        if existing is not None:
            if any(getattr(existing, k) != v for k, v in identity.items()):
                raise AttachmentError("conflict", 409)
            return existing
        row = AttachmentProviderRecording.objects.create(
            **identity,
            registered_output_id=uuid.uuid4(),
            source_authority_generation=connection.authority_generation,
            expires_at=min(
                timezone.now() + timedelta(hours=REGISTRATION_HOURS),
                session.expires_at,
                retention,
            ),
        )
        # Only a NEW registration wakes receipts. The same connection lock is
        # the absent-row rendezvous for event insertion and registration.
        AttachmentProviderEvent.objects.filter(
            connection=connection,
            pending_egress_id=egress_id,
            pending_ciphertext__isnull=False,
            pending_expires_at__gt=timezone.now(),
        ).update(pending_due_at=timezone.now())
        transaction.on_commit(lambda: _resume_registration(row))
    row.refresh_from_db()
    return row


def _lock_registrar(registrar, scope_id, session_id):
    from tracer.services.attachments.imports import _lock_creator

    scope = AttachmentScope.objects.get(pk=scope_id)
    _lock_creator(scope, registrar)
    return CaptureSession.objects.select_for_update().get(
        pk=session_id,
        scope_id=scope_id,
        writer_kind=registrar.kind,
        writer_id=registrar.id,
    )


def _check_connection(connection):
    if (
        not connection.enabled
        or connection.revoked_at
        or connection.provider != "livekit"
    ):
        raise AttachmentError("source_unavailable", 403)


def _parent_expiry(scope, registrar, trace_id, parent_span):
    from tracer.services.attachments.authority import LiveAuthority
    from tracer.services.attachments.occurrences import Parent

    state = LiveAuthority().source(
        SimpleNamespace(user_id=registrar.id if registrar.kind == "user" else None),
        scope,
        Parent(trace_id, parent_span),
    )
    if state is None or not state.readable or state.expires_at <= timezone.now():
        raise AttachmentError("source_unavailable", 403)
    return state.expires_at


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def verify_livekit_webhook(body, authorization):
    """Return (connection, event) for a genuinely signed LiveKit webhook.

    The JWT issuer selects the connection; the HS256 signature must verify with
    that connection's sealed secret and the ``sha256`` claim must equal the
    base64 SHA-256 of the exact body. Any failure is indistinguishable.
    """
    try:
        if not isinstance(body, bytes | bytearray) or len(body) > WEBHOOK_MAX_BYTES:
            raise ValueError
        if not isinstance(authorization, str) or len(authorization) > 8192:
            raise ValueError
        token = authorization.removeprefix("Bearer ").strip()
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        if not isinstance(issuer, str):
            raise ValueError
        connection = AttachmentProviderConnection.objects.filter(
            provider="livekit",
            api_key_id=issuer,
            enabled=True,
            revoked_at__isnull=True,
        ).first()
        if connection is None:
            raise ValueError
        secret = envelopes.open_bytes(
            connection.key_id,
            connection.secret_nonce,
            connection.secret_ciphertext,
            _aad(connection.scope_id, connection.id),
        )
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            options={"require": ["exp", "iss", "sha256"]},
            leeway=0,
        )
        expected = base64.b64encode(hashlib.sha256(bytes(body)).digest()).decode()
        if not hmac.compare_digest(str(claims["sha256"]), expected):
            raise ValueError
        event = _strict_json(bytes(body).decode())
        if not isinstance(event, dict):
            raise ValueError
        return connection, event
    except AttachmentError:
        raise AttachmentError("not_found", 404) from None
    except Exception:  # noqa: BLE001 - webhook verification boundary; fails closed as 404
        raise AttachmentError("not_found", 404) from None


def _state(value):
    if type(value) is int:
        value = _LIVEKIT_NUMERIC.get(value)
    return LIVEKIT_STATES.get(value) if isinstance(value, str) else None


def _int(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return int(value)
    return None


def _completion_metadata(registration, info):
    """Only a signed, exact registered output can select a retrieval locator."""
    results = info.get("fileResults")
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        return None
    result = results[0]
    size, duration_ns = _int(result.get("size")), _int(result.get("duration"))
    if (
        size is None
        or duration_ns is None
        or not 0 < size <= 1024**3
        or not 0 < duration_ns <= 7_200_000 * 1_000_000
    ):
        return None
    url = result.get("location")
    destination = safe_fetch.parse_destination(
        url, approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ())
    )
    source = safe_fetch.RangeSource(
        destination, size, 8 * 1024 * 1024, 30, 8 * 1024 * 1024
    )
    revision, length, mime = safe_fetch.inspect_metadata(source)
    if length != size or mime not in {
        "audio/wav",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/webm",
        "audio/flac",
    }:
        return None
    return {
        "url": url,
        "generation_kind": "http_strong_etag",
        "generation": revision,
        "source_bytes": size,
        "duration_ms": -(-duration_ns // 1_000_000),
        "mime_type": mime,
    }


def _apply_info(
    registration,
    info,
    *,
    metadata=None,
    scope=None,
    expires_at=None,
    capture_session_id=None,
):
    """Monotonic terminal state plus durable dispatch, in the receipt transaction."""
    state = _state(info.get("status"))
    updated = _int(info.get("updatedAt")) or _int(info.get("endedAt")) or 0
    room = info.get("roomName")
    if (
        state is None
        or not isinstance(room, str)
        or not hmac.compare_digest(_digest(room), registration.room_digest)
        or registration.provider_state in TERMINAL_PROVIDER_STATES
        or updated < registration.provider_updated_at
        or (
            state not in TERMINAL_PROVIDER_STATES
            and _RANK.get(state, 0) < _RANK.get(registration.provider_state, 0)
        )
    ):
        return False
    fields = ["provider_state", "provider_updated_at"]
    if state == ProviderRecordingState.COMPLETE:
        if metadata is None:
            state = ProviderRecordingState.FAILED
        else:
            envelope_id = envelopes.seal(
                scope=scope,
                creator_kind=registration.registrar_kind,
                creator_id=registration.registrar_id,
                direction=registration.direction,
                trace_id=registration.trace_id,
                span_id=registration.span_id,
                source_expires_at=expires_at,
                capture_session_id=capture_session_id,
                authority_generation=registration.source_authority_generation,
                **metadata,
            )
            registration.output_envelope_id = envelope_id
            registration.output_bytes = metadata["source_bytes"]
            registration.output_duration_ms = metadata["duration_ms"]
            registration.source_generation_kind = metadata["generation_kind"]
            registration.source_generation = metadata["generation"]
            registration.source_mime_type = metadata["mime_type"]
            registration.source_expires_at = expires_at
            registration.dispatch_due_at = (
                timezone.now() if registration.dispatch_mode == "auto" else None
            )
            fields += [
                "output_envelope_id",
                "output_bytes",
                "output_duration_ms",
                "source_generation_kind",
                "source_generation",
                "source_mime_type",
                "source_expires_at",
                "dispatch_due_at",
            ]
    registration.provider_state = state
    registration.provider_updated_at = max(updated, registration.provider_updated_at)
    registration.save(update_fields=fields)
    return True


def _process_observation(connection, event):
    """Verify first with verify_livekit_webhook. No source body is read here."""
    event_id, info = event.get("id"), event.get("egressInfo")
    if (
        not isinstance(event_id, str)
        or not 1 <= len(event_id) <= 64
        or event.get("event")
        not in {"egress_started", "egress_updated", "egress_ended"}
        or not isinstance(info, dict)
        or not isinstance(info.get("egressId"), str)
    ):
        return None
    row = AttachmentProviderRecording.objects.filter(
        connection_id=connection.id,
        scope_id=connection.scope_id,
        egress_id=info["egressId"],
        expires_at__gt=timezone.now(),
    ).first()
    if row is None:
        return None
    if AttachmentProviderEvent.objects.filter(
        connection=connection, event_id=event_id
    ).exists():
        return row
    registrar = SimpleNamespace(kind=row.registrar_kind, id=row.registrar_id)
    session = CaptureSession.objects.filter(
        scope_id=row.scope_id,
        client_id=row.capture_session_client_id,
        correlation_nonce=row.correlation_nonce,
        writer_kind=registrar.kind,
        writer_id=registrar.id,
    ).first()
    if session is None:
        raise AttachmentError("source_unavailable", 403)
    bootstrap.check_session(session)
    metadata = None
    scope = AttachmentScope.objects.get(pk=row.scope_id)
    expiry = min(
        row.expires_at,
        session.expires_at,
        _parent_expiry(scope, registrar, row.trace_id, row.span_id),
    )
    if (
        row.provider_state not in TERMINAL_PROVIDER_STATES
        and _state(info.get("status")) == ProviderRecordingState.COMPLETE
        and isinstance(info.get("roomName"), str)
        and hmac.compare_digest(_digest(info["roomName"]), row.room_digest)
    ):
        if not scope.allow_import or not bootstrap.session_policy(session).allow_import:
            raise AttachmentError("source_unavailable", 403)
        current = _live_connection(connection.id, row.scope_id)
        if (
            current.authority_generation != connection.authority_generation
            or row.source_authority_generation != current.authority_generation
        ):
            raise AttachmentError("source_unavailable", 403)
        metadata = _completion_metadata(row, info)
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=row.scope_id)
        session = _lock_registrar(registrar, row.scope_id, session.id)
        bootstrap.check_session(session)
        if not scope.allow_import or not bootstrap.session_policy(session).allow_import:
            raise AttachmentError("source_unavailable", 403)
        live = AttachmentProviderConnection.objects.select_for_update().get(
            pk=connection.id, scope_id=row.scope_id
        )
        _check_connection(live)
        if live.authority_generation != connection.authority_generation:
            raise AttachmentError("source_unavailable", 403)
        locked = AttachmentProviderRecording.objects.select_for_update().get(pk=row.pk)
        if (
            locked.source_authority_generation != live.authority_generation
            or locked.expires_at <= timezone.now()
        ):
            raise AttachmentError("source_unavailable", 403)
        try:
            with transaction.atomic():
                AttachmentProviderEvent.objects.create(
                    scope_id=locked.scope_id, connection=live, event_id=event_id
                )
        except IntegrityError:
            return locked
        _apply_info(
            locked,
            info,
            metadata=metadata,
            scope=scope,
            expires_at=expiry,
            capture_session_id=session.id,
        )
    # A crash here is harmless: COMPLETE + no job + dispatch_due_at is durable.
    return locked


def completed_output(importer, scope, *, connection_id, registered_output_id):
    connection = _live_connection(connection_id, scope.id)
    row = AttachmentProviderRecording.objects.filter(
        scope=scope,
        connection_id=connection_id,
        registered_output_id=registered_output_id,
        registrar_kind=importer.kind,
        registrar_id=importer.id,
        expires_at__gt=timezone.now(),
    ).first()
    if (
        row is None
        or row.provider_state != ProviderRecordingState.COMPLETE
        or not row.output_bytes
        or not row.output_envelope_id
        or row.source_authority_generation != connection.authority_generation
    ):
        raise AttachmentError("source_unavailable", 403)
    return row


def _registration_session(row, registrar, scope_id):
    if row.scope_id != scope_id or (row.registrar_kind, row.registrar_id) != (
        registrar.kind,
        registrar.id,
    ):
        raise AttachmentError("not_found", 404)
    return _registrar_session(
        registrar,
        scope_id,
        {
            "capture_session_id": str(row.capture_session_client_id),
            "correlation_nonce": str(row.correlation_nonce),
        },
    )


def lookup(registrar, *, scope_id, registered_output_id):
    row = AttachmentProviderRecording.objects.filter(
        scope_id=scope_id,
        registered_output_id=registered_output_id,
        registrar_kind=registrar.kind,
        registrar_id=registrar.id,
    ).first()
    if row is None:
        raise AttachmentError("not_found", 404)
    _registration_session(row, registrar, scope_id)
    connection = _live_connection(row.connection_id, scope_id)
    if (
        row.expires_at <= timezone.now()
        or row.source_authority_generation != connection.authority_generation
    ):
        raise AttachmentError("source_unavailable", 403)
    _resume_registration(row)  # at most one due receipt; no expiry/claim rearm
    row.refresh_from_db()
    return row


def registration_document(row, *, registrar, scope_id):
    """Authoritative closed control projection, never a source capability."""
    from tracer.services.attachments import imports, source_providers, wire
    from tracer.services.attachments.interfaces import SourceRef

    session = _registration_session(row, registrar, scope_id)
    scope = AttachmentScope.objects.get(pk=scope_id)
    _parent_expiry(scope, registrar, row.trace_id, row.span_id)
    ref = SourceRef("ProviderFile", row.id)
    facts = None
    if row.provider_state == "complete":
        facts = source_providers.resolve(
            source=ref,
            scope_id=scope_id,
            importer=registrar,
            capture_session_id=session.id,
        )
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        session = _lock_registrar(registrar, scope_id, session.id)
        connection, _envelope, locked = source_providers.lock_source(
            scope_id=scope_id, source=ref
        )
        bootstrap.check_session(session)
        _check_connection(connection)
        if (
            locked.expires_at <= timezone.now()
            or locked.source_authority_generation != connection.authority_generation
        ):
            raise AttachmentError("source_unavailable", 403)
        # A concurrent completion requires fresh outside-lock metadata resolution.
        if (locked.provider_state == "complete") != (facts is not None):
            raise AttachmentError("conflict", 409)
        if not re.fullmatch(
            r"EG_[A-Za-z0-9]{1,61}", locked.egress_id
        ) or not re.fullmatch(r"[0-9a-f]{64}", locked.room_digest):
            raise AttachmentError("source_unavailable", 403)
        result = {
            "source": {
                "kind": "ProviderFile",
                "connection_id": str(locked.connection_id),
                "registered_output_id": str(locked.registered_output_id),
            },
            "state": locked.provider_state,
            "dispatch_mode": locked.dispatch_mode,
            "binding": {
                "provider": "livekit",
                "egress_id": locked.egress_id,
                "room_digest": locked.room_digest,
            },
        }
        if facts is not None:
            source_providers.revalidate(
                facts=facts,
                scope_id=scope_id,
                importer=registrar,
                capture_session_id=session.id,
            )
            body = _dispatch_body(locked, session)
            wire.validate("RecordingImportRequest", body)
            imports._check_registered_descriptor(locked, session, body["recording"])
            job = imports._registered_job(
                locked, session, registrar, body["recording"], facts, link=False
            )
            result["import_request"] = body
            if job is not None and locked.import_job_id == job.id:
                result["job_id"] = str(job.id)
        elif locked.import_job_id is not None:
            raise AttachmentError("conflict", 409)
        return result


def inspect_envelope(registrar, *, scope_id, envelope_id):
    """Inspect an opaque envelope and its unique authenticated ancestry."""
    from tracer.services.attachments import source_providers
    from tracer.services.attachments.interfaces import SourceRef

    envelope = envelopes.describe(scope_id=scope_id, envelope_id=envelope_id)
    if (envelope.creator_kind, envelope.creator_id) != (registrar.kind, registrar.id):
        raise AttachmentError("not_found", 404)
    # The session comes from sealed provenance, never a caller's assertion.
    session_id = uuid.UUID(envelopes._document(envelope)["capture_session_id"])
    facts = source_providers.resolve(
        source=SourceRef("RemoteUrlEnvelope", envelope.id),
        scope_id=scope_id,
        importer=registrar,
        capture_session_id=session_id,
    )
    ancestor = source_providers._envelope_provider(scope_id, envelope.id)
    document = (
        registration_document(ancestor, registrar=registrar, scope_id=scope_id)
        if ancestor is not None
        else None
    )
    with transaction.atomic():
        AttachmentScope.objects.select_for_update().get(pk=scope_id)
        _lock_registrar(registrar, scope_id, session_id)
        _, _, current = source_providers.lock_source(
            scope_id=scope_id, source=SourceRef("RemoteUrlEnvelope", envelope.id)
        )
        if (current.id if current else None) != (ancestor.id if ancestor else None):
            raise AttachmentError("conflict", 409)
        source_providers.revalidate(
            facts=facts,
            scope_id=scope_id,
            importer=registrar,
            capture_session_id=session_id,
        )
        if current is not None and current.import_job_id != ancestor.import_job_id:
            raise AttachmentError("conflict", 409)
    return {
        "source": {"kind": "RemoteUrlEnvelope", "envelope_id": str(envelope.id)},
        "registration": document,
    }


def reader_qualified(registration):
    return (
        registration.connection.provider in READERS
        and registration.output_envelope_id is not None
    )


def open_reader(registration, limits=None, *, job_id=None, fence=None):
    # Legacy callers without an owner reservation cannot read even one byte.
    if job_id is None or fence is None:
        raise AttachmentError("source_unavailable", 403)
    from tracer.models.attachment_recording import AttachmentImportJob

    if not AttachmentImportJob.objects.filter(
        pk=job_id, provider_recording_id=registration.id, scope_id=registration.scope_id
    ).exists():
        raise AttachmentError("source_unavailable", 403)
    return _open_livekit_source(job_id=job_id, fence=fence)


def _dispatch_body(row, session):
    return {
        "mode": "completed_recording",
        "recording": {
            "schema_version": 1,
            "association": {
                "capture_session_id": str(row.capture_session_client_id),
                "correlation_nonce": str(row.correlation_nonce),
                "parent": {"trace_id": row.trace_id.hex, "span_id": row.span_id},
            },
            "policy_version": str(session.policy_version),
            "deletion_generation": session.deletion_generation,
            "mime_type": row.source_mime_type,
            "direction": row.direction,
            "source_kind": "ProviderFile",
            "recording_id": str(row.recording_client_id),
            "modality": "audio",
            "declared_bytes": row.output_bytes,
            "declared_duration_ms": row.output_duration_ms,
        },
        "source": {
            "kind": "ProviderFile",
            "connection_id": str(row.connection_id),
            "registered_output_id": str(row.registered_output_id),
        },
    }


def dispatch_completed(*, limit=2, now=None):
    """Claim a bounded indexed cursor before admission; no lock inversion.

    Capacity exhaustion or a crash after submit is retried with the same client
    identity. The F01 idempotent response supplies job_id (not a model object).
    """
    from tracer.services.attachments import imports

    now = now or timezone.now()
    stats = {"dispatched": 0, "deferred": 0, "denied": 0}
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid dispatch limit")
    with transaction.atomic():
        rows = list(
            AttachmentProviderRecording.objects.select_for_update(skip_locked=True)
            .filter(
                dispatch_mode="auto",
                provider_state="complete",
                import_job_id__isnull=True,
                dispatch_due_at__lte=now,
            )
            .order_by("dispatch_due_at", "id")[:limit]
        )
        for row in rows:
            row.dispatch_attempts += 1
            row.dispatch_due_at = min(
                row.expires_at,
                row.source_expires_at or row.expires_at,
                now
                + timedelta(
                    seconds=min(300, 5 * 2 ** min(row.dispatch_attempts - 1, 6))
                ),
            )
            row.save(update_fields=["dispatch_attempts", "dispatch_due_at"])
    for row in rows:
        try:
            registrar = imports.Importer(row.registrar_kind, row.registrar_id)
            session = CaptureSession.objects.filter(
                scope_id=row.scope_id,
                client_id=row.capture_session_client_id,
                correlation_nonce=row.correlation_nonce,
                writer_kind=registrar.kind,
                writer_id=registrar.id,
            ).first()
            source_deadline = min(
                row.expires_at, row.source_expires_at or row.expires_at
            )
            if session is None or source_deadline <= now:
                raise AttachmentError("expired", 410)
            bootstrap.check_session(session)
            # Crucially outside the provider-row transaction: submit locks
            # budgets -> scope -> creator/session -> connection/source -> job.
            if imports.recover_auto_dispatch(row, registrar) is not None:
                stats["dispatched"] += 1
                continue
            projection = registration_document(
                row, registrar=registrar, scope_id=row.scope_id
            )
            document, _created = imports.submit(
                registrar, scope_id=row.scope_id, body=projection["import_request"]
            )
            job_id = uuid.UUID(document["job_id"])
            # submit commits allocation/reservation/link in one transaction,
            # including exact legacy envelope aliases. There is no second-link
            # crash window and no reverse provider -> job lock acquisition.
            if not AttachmentProviderRecording.objects.filter(
                pk=row.pk,
                scope_id=row.scope_id,
                import_job_id=job_id,
                dispatch_due_at__isnull=True,
            ).exists():
                raise AttachmentError("conflict", 409)
            stats["dispatched"] += 1
        except AttachmentError as error:
            # Unknown/unavailable authority (including a historical 403) must
            # retain its bounded retry cursor. Authorization still fails closed.
            # Only explicit terminal evidence can permanently clear dispatch.
            terminal = error.code in {
                "expired",
                "deleted",
                "policy_denied",
                "permission_denied",
                "credential_scope_unsupported",
                "recording_disabled",
                "snapshot_changed",
                "validation_failed",
                "unsupported_media",
            }
            if not terminal or error.status == 429 or error.status >= 500:
                stats["deferred"] += 1
            else:
                AttachmentProviderRecording.objects.filter(
                    pk=row.pk,
                    import_job_id__isnull=True,
                    dispatch_attempts=row.dispatch_attempts,
                ).update(dispatch_due_at=None)
                stats["denied"] += 1
        except (
            Exception
        ):  # Retry cursor survives DB/transport/process failures; never log payloads.
            stats["deferred"] += 1
    return stats


def reconcile_registered(*, limit=2):
    """Reobserve only fixed sealed outputs, never LiveKit List/Start/Stop.

    LiveKit offers no approved fixed-ID status API here. Signed events establish
    completion; completed/no-job rows are recovered independently by dispatch.
    This bounded reconciliation checks their pinned metadata before retrying.
    """
    stats = {"observed": 0, "unavailable": 0}
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid reconciliation limit")
    rows = AttachmentProviderRecording.objects.filter(
        dispatch_mode="auto",
        provider_state="complete",
        import_job_id__isnull=True,
        dispatch_due_at__lte=timezone.now(),
    ).order_by("dispatch_due_at", "id")[:limit]
    for row in rows:
        try:
            connection = _live_connection(row.connection_id, row.scope_id)
            if row.source_authority_generation != connection.authority_generation:
                raise AttachmentError("source_unavailable", 403)
            from tracer.services.attachments.interfaces import SourceRef
            from tracer.services.attachments.source_providers import resolve

            registrar = SimpleNamespace(kind=row.registrar_kind, id=row.registrar_id)
            session = CaptureSession.objects.filter(
                scope_id=row.scope_id,
                client_id=row.capture_session_client_id,
                correlation_nonce=row.correlation_nonce,
                writer_kind=registrar.kind,
                writer_id=registrar.id,
            ).first()
            if session is None:
                raise AttachmentError("source_unavailable", 403)
            resolve(
                source=SourceRef("ProviderFile", row.id),
                scope_id=row.scope_id,
                importer=registrar,
                capture_session_id=session.id,
            )
            url = envelopes.open_url(
                scope_id=row.scope_id, envelope_id=row.output_envelope_id
            )
            source = safe_fetch.RangeSource(
                safe_fetch.parse_destination(
                    url,
                    approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ()),
                ),
                row.output_bytes,
                8 * 1024 * 1024,
                30,
                8 * 1024 * 1024,
                generation=row.source_generation,
                generation_kind=row.source_generation_kind,
            )
            if safe_fetch.inspect_metadata(source) != (
                row.source_generation,
                row.output_bytes,
                row.source_mime_type,
            ):
                raise AttachmentError("snapshot_changed", 409)
            stats["observed"] += 1
        except AttachmentError:
            stats["unavailable"] += 1
    return stats


def _pending_aad(row):
    return f"fi.attachments.v1.pending:{row.scope_id}:{row.connection_id}:{row.id}:{row.event_id}".encode()


def _clear_pending(row):
    for field in _PENDING_FIELDS:
        setattr(row, field, None)
    row.save(update_fields=_PENDING_FIELDS)


def _pending_plaintext(connection, info):
    """Retain only bounded supported, signed completions, never a JWT."""
    if (
        _state(info.get("status")) != ProviderRecordingState.COMPLETE
        or not isinstance(info.get("egressId"), str)
        or not re.fullmatch(r"EG_[A-Za-z0-9]{1,61}", info["egressId"])
        or not isinstance(info.get("roomName"), str)
        or not 1 <= len(info["roomName"]) <= 256
    ):
        return None
    _digest(info["roomName"])
    results = info.get("fileResults")
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        return None
    size, duration = _int(results[0].get("size")), _int(results[0].get("duration"))
    if (
        size is None
        or duration is None
        or not 0 < size <= 1024**3
        or not 0 < duration <= 7_200_000 * 1_000_000
    ):
        return None
    safe_fetch.parse_destination(
        results[0].get("location"),
        approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ()),
    )
    try:
        raw = json.dumps(
            {
                "egressInfo": info,
                "authority_generation": connection.authority_generation,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise AttachmentError("validation_failed", 422) from None
    if len(raw) > 65536:
        raise AttachmentError("too_large", 413)
    updated = _int(info.get("updatedAt")) or _int(info.get("endedAt")) or 0
    if not 0 <= updated <= 2**63 - 1:
        return None
    # A signed EgressInfo can still contain an output request with provider
    # credentials. Signature verification authenticates it, but does NOT make
    # arbitrary extra fields appropriate for durable retention. Seal only the
    # closed completion projection consumed by _apply_info/_completion_metadata.
    document = {
        "egressInfo": {
            "egressId": info["egressId"],
            "roomName": info["roomName"],
            "status": "EGRESS_COMPLETE",
            "updatedAt": updated,
            "fileResults": [{"location": results[0]["location"], "size": size, "duration": duration}],
        },
        "authority_generation": connection.authority_generation,
    }
    plaintext = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(plaintext) > 65536:
        raise AttachmentError("too_large", 413)
    return plaintext


def process_livekit_event(connection, event):
    """Called only after exact-raw-body JWT verification; never reads media."""
    event_id, info = event.get("id"), event.get("egressInfo")
    if (
        not isinstance(event_id, str)
        or not 1 <= len(event_id) <= 64
        or event.get("event")
        not in {"egress_started", "egress_updated", "egress_ended"}
        or not isinstance(info, dict)
    ):
        return None
    if _state(info.get("status")) != ProviderRecordingState.COMPLETE:
        return _process_observation(connection, event)
    plaintext = _pending_plaintext(connection, info)
    if plaintext is None:
        return None
    now = timezone.now()
    expiry = now + timedelta(seconds=PENDING_TTL_SECONDS)
    # Parent lookup can perform IO; do it before the rendezvous transaction.
    before = AttachmentProviderRecording.objects.filter(
        connection_id=connection.id,
        scope_id=connection.scope_id,
        egress_id=info["egressId"],
    ).first()
    if before is not None:
        registrar = SimpleNamespace(kind=before.registrar_kind, id=before.registrar_id)
        session = _registration_session(before, registrar, connection.scope_id)
        scope = AttachmentScope.objects.get(pk=connection.scope_id)
        expiry = min(
            expiry,
            before.expires_at,
            session.expires_at,
            _parent_expiry(scope, registrar, before.trace_id, before.span_id),
        )
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=connection.scope_id)
        if not scope.enabled:
            raise AttachmentError("disabled", 403)
        live = AttachmentProviderConnection.objects.select_for_update().get(
            pk=connection.id, scope_id=scope.id
        )
        _check_connection(live)
        if live.authority_generation != connection.authority_generation:
            raise AttachmentError("source_unavailable", 403)
        duplicate = (
            AttachmentProviderEvent.objects.select_for_update()
            .filter(connection=live, event_id=event_id)
            .first()
        )
        if duplicate is None:
            # Clears payload only, retaining the append-only replay identity.
            AttachmentProviderEvent.objects.filter(
                connection=live, pending_expires_at__lte=now
            ).update(**dict.fromkeys(_PENDING_FIELDS))
            if (
                AttachmentProviderEvent.objects.filter(
                    connection=live, pending_ciphertext__isnull=False
                ).count()
                >= MAX_PENDING_PER_CONNECTION
            ):
                raise AttachmentError("throttled", 429)
            current = AttachmentProviderRecording.objects.filter(
                connection=live, egress_id=info["egressId"]
            ).first()
            if current is not None:
                expiry = min(expiry, current.expires_at)
            if expiry <= now:
                return current
            receipt = AttachmentProviderEvent(
                id=uuid.uuid4(),
                scope=scope,
                connection=live,
                event_id=event_id,
                pending_egress_id=info["egressId"],
                pending_expires_at=expiry,
                pending_due_at=now,
            )
            (
                receipt.pending_key_id,
                receipt.pending_nonce,
                receipt.pending_ciphertext,
            ) = envelopes.seal_bytes(plaintext, _pending_aad(receipt))
            receipt.save(force_insert=True)
        # A duplicate never resets expiry or the durable claim. Cleared legacy
        # receipts remain inert even if a provider redelivers a new payload.
        transaction.on_commit(lambda: _try_pending(scope.id, live.id, event_id))
    return AttachmentProviderRecording.objects.filter(
        connection_id=connection.id, egress_id=info["egressId"]
    ).first()


def _try_pending(scope_id, connection_id, event_id):
    try:
        return apply_pending_completion(
            scope_id=scope_id, connection_id=connection_id, event_id=event_id
        )
    except (
        Exception
    ):  # Durable claim retries transport/authority failures, no payload logging.
        return None


def _resume_registration(row):
    receipt = (
        AttachmentProviderEvent.objects.filter(
            scope_id=row.scope_id,
            connection_id=row.connection_id,
            pending_egress_id=row.egress_id,
            pending_due_at__lte=timezone.now(),
            pending_ciphertext__isnull=False,
        )
        .order_by("pending_due_at", "id")
        .first()
    )
    if receipt is not None:
        return _try_pending(row.scope_id, row.connection_id, receipt.event_id)
    return None


def apply_pending_completion(*, scope_id, connection_id, event_id, now=None):
    """Claim -> metadata outside locks -> ordered atomic apply. Crash retry is durable."""
    from tracer.models.attachment_recording import AttachmentSourceEnvelope

    now = now or timezone.now()
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        live = AttachmentProviderConnection.objects.select_for_update().get(
            pk=connection_id, scope_id=scope_id
        )
        receipt = (
            AttachmentProviderEvent.objects.select_for_update()
            .filter(scope_id=scope_id, connection_id=connection_id, event_id=event_id)
            .first()
        )
        if receipt is None or receipt.pending_ciphertext is None:
            return None
        if receipt.pending_expires_at <= now:
            _clear_pending(receipt)
            return None
        if receipt.pending_due_at > now:
            return None
        _check_connection(live)
        if not scope.enabled:
            raise AttachmentError("disabled", 403)
        receipt.pending_due_at = min(
            receipt.pending_expires_at, now + timedelta(seconds=PENDING_RETRY_SECONDS)
        )
        receipt.save(update_fields=["pending_due_at"])
        claim = receipt.pending_due_at
        row = AttachmentProviderRecording.objects.filter(
            scope_id=scope_id,
            connection_id=connection_id,
            egress_id=receipt.pending_egress_id,
        ).first()
    if row is None:
        return None  # registration's commit wakes this receipt, or tick retries
    document = _strict_json(
        envelopes.open_bytes(
            receipt.pending_key_id,
            receipt.pending_nonce,
            receipt.pending_ciphertext,
            _pending_aad(receipt),
        ).decode("utf-8")
    )
    if not isinstance(document, dict) or set(document) != {
        "egressInfo",
        "authority_generation",
    }:
        raise AttachmentError("source_unavailable", 403)
    info = document["egressInfo"]
    if (
        not isinstance(info, dict)
        or _pending_plaintext(live, info) is None
        or document["authority_generation"] != live.authority_generation
        or row.source_authority_generation != live.authority_generation
    ):
        raise AttachmentError("source_unavailable", 403)
    if (
        info["egressId"] != row.egress_id
        or _digest(info["roomName"]) != row.room_digest
    ):
        raise AttachmentError("source_unavailable", 403)
    registrar = SimpleNamespace(kind=row.registrar_kind, id=row.registrar_id)
    session = _registration_session(row, registrar, scope_id)
    if not scope.allow_import or not bootstrap.session_policy(session).allow_import:
        raise AttachmentError("source_unavailable", 403)
    expiry = min(
        receipt.pending_expires_at,
        row.expires_at,
        session.expires_at,
        _parent_expiry(scope, registrar, row.trace_id, row.span_id),
    )
    metadata = None
    if row.provider_state not in TERMINAL_PROVIDER_STATES:
        metadata = _completion_metadata(row, info)  # approved-host HEAD, never body
        if metadata is None:
            raise AttachmentError("source_unavailable", 403)
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        session = _lock_registrar(registrar, scope_id, session.id)
        bootstrap.check_session(session)
        if not scope.allow_import or not bootstrap.session_policy(session).allow_import:
            raise AttachmentError("source_unavailable", 403)
        live = AttachmentProviderConnection.objects.select_for_update().get(
            pk=connection_id, scope_id=scope_id
        )
        _check_connection(live)
        if row.output_envelope_id is not None:
            list(
                AttachmentSourceEnvelope.objects.select_for_update().filter(
                    pk=row.output_envelope_id, scope_id=scope_id
                )
            )
        locked = AttachmentProviderRecording.objects.select_for_update().get(
            pk=row.id, scope_id=scope_id
        )
        pending = AttachmentProviderEvent.objects.select_for_update().get(pk=receipt.id)
        current_time = timezone.now()
        if pending.pending_ciphertext is None or pending.pending_due_at != claim:
            return None
        if pending.pending_expires_at <= current_time:
            _clear_pending(pending)
            return None
        if (
            live.authority_generation != document["authority_generation"]
            or locked.source_authority_generation != live.authority_generation
            or locked.expires_at <= current_time
            or expiry <= current_time
            or locked.egress_id != info["egressId"]
            or locked.room_digest != _digest(info["roomName"])
            or locked.output_envelope_id != row.output_envelope_id
        ):
            raise AttachmentError("source_unavailable", 403)
        _apply_info(
            locked,
            info,
            metadata=metadata,
            scope=scope,
            expires_at=min(expiry, session.expires_at),
            capture_session_id=session.id,
        )
        _clear_pending(pending)
    return locked


def resume_pending_completions(*, limit=2, now=None):
    if type(limit) is not int or not 1 <= limit <= 2:
        raise ValueError("invalid pending limit")
    now = now or timezone.now()
    ids = list(
        AttachmentProviderEvent.objects.filter(
            pending_due_at__lte=now, pending_ciphertext__isnull=False
        )
        .order_by("pending_due_at", "id")
        .values_list("scope_id", "connection_id", "event_id")[:limit]
    )
    stats = {"applied": 0, "deferred": 0}
    for scope_id, connection_id, event_id in ids:
        try:
            row = apply_pending_completion(
                scope_id=scope_id,
                connection_id=connection_id,
                event_id=event_id,
                now=now,
            )
            stats["applied" if row is not None else "deferred"] += 1
        except (
            Exception
        ):  # Recovery is per receipt; never expose locator-bearing errors.
            stats["deferred"] += 1
    return stats


def sweep_pending_completions(*, limit=500, now=None):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid sweep limit")
    now = now or timezone.now()
    rows = list(
        AttachmentProviderEvent.objects.filter(
            pending_expires_at__lte=now, pending_ciphertext__isnull=False
        )
        .order_by("pending_expires_at", "id")
        .values_list("scope_id", "connection_id", "id")[:limit]
    )
    cleared = 0
    for scope_id, connection_id, receipt_id in rows:
        with transaction.atomic():
            AttachmentScope.objects.select_for_update().get(pk=scope_id)
            AttachmentProviderConnection.objects.select_for_update().get(
                pk=connection_id, scope_id=scope_id
            )
            row = AttachmentProviderEvent.objects.select_for_update().get(pk=receipt_id)
            if row.pending_ciphertext is not None and row.pending_expires_at <= now:
                _clear_pending(row)
                cleared += 1
    return cleared

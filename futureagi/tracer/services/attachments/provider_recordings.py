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

Reading the output bytes needs a provider output reader with exact-length,
immutable-version and bounded-range guarantees. None is qualified yet, so
``reader_qualified`` is False and admission fails ``source_unavailable``.
"""

import base64
import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import IO, Protocol

import jwt
from django.db import IntegrityError, transaction
from django.utils import timezone

from tracer.models.attachment import AttachmentScope, CaptureSession
from tracer.models.attachment_recording import (
    TERMINAL_PROVIDER_STATES,
    AttachmentProviderConnection,
    AttachmentProviderEvent,
    AttachmentProviderRecording,
    ProviderRecordingState,
)
from tracer.services.attachments import envelopes
from tracer.services.attachments.contract import AttachmentError, span_id, trace_uuid

WEBHOOK_MAX_BYTES = 64 * 1024
REGISTRATION_HOURS = 24
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


class ProviderOutput(Protocol):
    """What a provider output reader returns (``imports._fetch`` drives it)."""

    def read_range(self, offset: int, length: int, sink: IO[bytes]) -> None: ...


# Provider output readers: provider -> callable(registration, limits) returning
# a ``ProviderOutput``. Empty until qualified.
READERS: dict[str, Callable[..., ProviderOutput]] = {}


def _aad(scope_id, connection_id):
    return f"fi.attachments.v1.connection:{scope_id}:{connection_id}".encode()


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


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
    ).update(enabled=False, revoked_at=timezone.now())


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
):
    """Register an EXISTING egress by fixed ID. Never starts or lists Egress.

    ``registrar`` is an authenticated original identity; a write-only upload
    capability cannot reach this function or select a connection.
    """
    connection = _live_connection(connection_id, scope_id)
    if (
        not isinstance(egress_id, str)
        or not re.fullmatch(r"EG_[A-Za-z0-9]{1,61}", egress_id)
        or not isinstance(room_name, str)
        or not 1 <= len(room_name) <= 256
        or direction not in {"input", "output", "mixed"}
    ):
        raise AttachmentError("validation_failed", 422)
    parent = association["parent"]
    _registrar_session(registrar, scope_id, association)
    try:
        return AttachmentProviderRecording.objects.create(
            scope_id=scope_id,
            connection=connection,
            registered_output_id=uuid.uuid4(),
            egress_id=egress_id,
            room_digest=_digest(room_name),
            registrar_kind=registrar.kind,
            registrar_id=registrar.id,
            recording_client_id=uuid.UUID(str(recording_id)),
            trace_id=trace_uuid(parent["trace_id"]),
            span_id=span_id(parent["span_id"]),
            capture_session_client_id=uuid.UUID(association["capture_session_id"]),
            correlation_nonce=uuid.UUID(association["correlation_nonce"]),
            direction=direction,
            expires_at=timezone.now() + timedelta(hours=REGISTRATION_HOURS),
        )
    except IntegrityError:
        raise AttachmentError("conflict", 409) from None


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def verify_livekit_webhook(body, authorization):
    """Return (connection, event) for a genuinely signed LiveKit webhook.

    The JWT issuer selects the connection; the HS256 signature must verify with
    that connection's sealed secret and the ``sha256`` claim must equal the
    base64 SHA-256 of the exact body. Any failure is indistinguishable.
    """
    try:
        if not isinstance(body, bytes | bytearray) or len(body) > WEBHOOK_MAX_BYTES:
            raise ValueError
        token = (authorization or "").removeprefix("Bearer ").strip()
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
            leeway=30,
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
    if isinstance(value, int):
        value = _LIVEKIT_NUMERIC.get(value)
    return LIVEKIT_STATES.get(value)


def _int(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return int(value)
    return None


def _apply_info(registration, info):
    """Monotonic, terminal-sticky update from one verified egress snapshot."""
    state = _state(info.get("status"))
    updated = _int(info.get("updatedAt")) or _int(info.get("endedAt")) or 0
    room = info.get("roomName")
    if state is None or not isinstance(room, str):
        return False
    if not hmac.compare_digest(_digest(room), registration.room_digest):
        return False  # Wrong room: same egress id cannot complete this binding.
    if registration.provider_state in TERMINAL_PROVIDER_STATES:
        return False
    if state not in TERMINAL_PROVIDER_STATES and (
        updated < registration.provider_updated_at
        or _RANK.get(state, 0) < _RANK.get(registration.provider_state, 0)
    ):
        return False
    fields = ["provider_state", "provider_updated_at"]
    if state == ProviderRecordingState.COMPLETE:
        results = info.get("fileResults")
        if not isinstance(results, list) or len(results) != 1:
            state = ProviderRecordingState.FAILED
        else:
            size = _int(results[0].get("size"))
            duration_ns = _int(results[0].get("duration"))
            if not size or not duration_ns:
                state = ProviderRecordingState.FAILED
            else:
                registration.output_bytes = size
                registration.output_duration_ms = -(-duration_ns // 1_000_000)
                fields += ["output_bytes", "output_duration_ms"]
    registration.provider_state = state
    registration.provider_updated_at = max(updated, registration.provider_updated_at)
    registration.save(update_fields=fields)
    return True


def process_livekit_event(connection, event):
    """Apply one verified event once. Unknown/foreign egress IDs do nothing."""
    event_id = event.get("id")
    info = event.get("egressInfo")
    if (
        not isinstance(event_id, str)
        or not 1 <= len(event_id) <= 64
        or event.get("event")
        not in {"egress_started", "egress_updated", "egress_ended"}
        or not isinstance(info, dict)
        or not isinstance(info.get("egressId"), str)
    ):
        return None
    with transaction.atomic():
        try:
            with transaction.atomic():
                AttachmentProviderEvent.objects.create(
                    scope_id=connection.scope_id,
                    connection=connection,
                    event_id=event_id,
                )
        except IntegrityError:
            return None  # Replay/duplicate delivery.
        registration = (
            AttachmentProviderRecording.objects.select_for_update()
            .filter(
                connection=connection,
                scope_id=connection.scope_id,
                egress_id=info["egressId"],
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if registration is None or not _apply_info(registration, info):
            return registration
    _enqueue_import(registration)
    return registration


def reconcile(registration, status_client):
    """Fixed-ID status reconciliation, e.g. after a lost webhook.

    ``status_client.get(egress_id)`` must query ONE egress by its fixed ID and
    return its info dict, or None when the provider API cannot say. Never a
    generic list. ``None`` leaves the registration unchanged.
    """
    connection = _live_connection(registration.connection_id, registration.scope_id)
    info = status_client.get(registration.egress_id)
    if not isinstance(info, dict) or info.get("egressId") != registration.egress_id:
        return registration
    with transaction.atomic():
        locked = AttachmentProviderRecording.objects.select_for_update().get(
            pk=registration.pk, connection=connection
        )
        changed = _apply_info(locked, info)
    if changed:
        _enqueue_import(locked)
    return locked


def _enqueue_import(registration):
    """Exactly one import per completed registration; admission still applies."""
    from tracer.services.attachments import imports

    if registration.provider_state != ProviderRecordingState.COMPLETE:
        return None
    with transaction.atomic():
        locked = AttachmentProviderRecording.objects.select_for_update().get(
            pk=registration.pk
        )
        if locked.import_job_id is not None:
            return locked.import_job_id
        scope = AttachmentScope.objects.get(pk=locked.scope_id)
        body = {
            "mode": "completed_recording",
            "recording": {
                "schema_version": 1,
                "association": {
                    "capture_session_id": str(locked.capture_session_client_id),
                    "correlation_nonce": str(locked.correlation_nonce),
                    "parent": {
                        "trace_id": locked.trace_id.hex,
                        "span_id": locked.span_id,
                    },
                },
                "policy_version": imports.policy_token(scope),
                "deletion_generation": scope.deletion_generation,
                "mime_type": "audio/wav",
                "direction": locked.direction,
                "source_kind": "ProviderFile",
                "recording_id": str(locked.recording_client_id),
                "modality": "audio",
                "declared_bytes": locked.output_bytes,
                "declared_duration_ms": locked.output_duration_ms,
            },
            "source": {
                "kind": "ProviderFile",
                "connection_id": str(locked.connection_id),
                "registered_output_id": str(locked.registered_output_id),
            },
        }
        try:
            _, job = imports.submit(
                imports.Importer(locked.registrar_kind, locked.registrar_id),
                scope_id=locked.scope_id,
                body=body,
            )
        except AttachmentError:
            return None  # Denied/unsupported: nothing is fetched or retained.
        locked.import_job_id = job.id
        locked.save(update_fields=["import_job_id"])
        return job.id


def completed_output(importer, scope, *, connection_id, registered_output_id):
    """Authorize a ProviderFile source: same scope, live connection, registrar."""
    _live_connection(connection_id, scope.id)
    registration = AttachmentProviderRecording.objects.filter(
        scope=scope,
        connection_id=connection_id,
        registered_output_id=registered_output_id,
        registrar_kind=importer.kind,
        registrar_id=importer.id,
        expires_at__gt=timezone.now(),
    ).first()
    if (
        registration is None
        or registration.provider_state != ProviderRecordingState.COMPLETE
        or not registration.output_bytes
    ):
        raise AttachmentError("source_unavailable", 403)
    return registration


def reader_qualified(registration):
    return registration.connection.provider in READERS


def open_reader(registration, limits):
    _live_connection(registration.connection_id, registration.scope_id)
    reader = READERS.get(registration.connection.provider)
    if reader is None:
        raise AttachmentError("source_unavailable", 403)
    return reader(registration, limits)

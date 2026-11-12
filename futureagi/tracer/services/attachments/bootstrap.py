"""Narrow broker, separate from APIKeyAuthentication and unrelated REST APIs.

The service takes an already-authenticated principal. It never authenticates an
ID supplied in a request body. Restricted credentials additionally require an
explicit, live, owner-managed AttachmentWriteGrant for this exact scope.

A capture session is the SDK's runtime session: ``capture_session_id`` and the
non-secret ``correlation_nonce`` are chosen by the client, are unique per scope
and are bound to the authenticated writer. Repeating the bootstrap with the same
pair from the same writer renews the session (BR-2); any other writer conflicts.
"""

import base64
import hmac
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models import OrgApiKey, User
from accounts.models.workspace import Workspace
from tracer.models.attachment import (
    AttachmentScope,
    AttachmentWriteGrant,
    CaptureSession,
)
from tracer.models.project import PROJECT_TYPES, Project
from tracer.services.attachments.contract import (
    AttachmentError,
    CapturePolicy,
    fingerprint,
    policy_document,
    server_policy,
)

CAPABILITY_SALT = "fi.attachments.v1.write"
CAPABILITY_PREFIX = "fiac1"
CAPABILITY_SECONDS = 300
SESSION_SECONDS = 3600
WRITE_OPERATIONS = ("upload", "complete", "abort", "status")

# Finite safety envelopes the server actually enforces, not edition limits.
LIMITS = {
    "max_upload_bytes": 8 * 1024 * 1024,
    "segment_bytes": 8 * 1024 * 1024,
    "segment_duration_ms": 30000,
    "recording_bytes": 1024 * 1024 * 1024,
    "recording_duration_ms": 7200000,
    "active_transfers": 16,
    "active_recordings": 4,
    "reserved_bytes": 1024 * 1024 * 1024,
    "validation_jobs": 1024,
    "initializations_per_minute": 120,
    "links_per_minute": 120,
    "upload_bytes_per_second": 8 * 1024 * 1024,
    "playback_bytes_per_second": 8 * 1024 * 1024,
    "transfer_timeout_seconds": 60,
    "grant_ttl_seconds": 60,
    "revocation_check_seconds": 5,
}


@dataclass(frozen=True)
class Writer:
    kind: str
    id: UUID
    organization_id: UUID
    workspace_id: UUID


def check_writer(writer, scope):
    if (writer.organization_id, writer.workspace_id) != (
        scope.organization_id,
        scope.workspace_id,
    ):
        raise AttachmentError("not_found", 404)
    if not Workspace.objects.filter(
        pk=scope.workspace_id, organization_id=scope.organization_id, is_active=True
    ).exists():
        raise AttachmentError("scope_unresolved", 404)
    if not Project.no_workspace_objects.filter(
        pk=scope.project_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        deleted=False,
    ).exists():
        raise AttachmentError("not_found", 404)
    _check_identity(writer, scope.workspace, scope)


def _check_identity(writer, workspace, scope=None):
    """Write eligibility of the authenticated identity for ``workspace``.

    ``scope`` is None before the project has an attachment scope. A restricted
    key's grant names a scope, so without one it is refused here; that keeps a
    read-only member or an ungranted key from creating any row (``resolve_scope``).
    """
    if writer.kind == "api_key":
        key = OrgApiKey.no_workspace_objects.filter(
            pk=writer.id,
            organization_id=writer.organization_id,
            enabled=True,
            deleted=False,
        ).first()
        if key is None or (
            key.workspace_id and key.workspace_id != writer.workspace_id
        ):
            raise AttachmentError("credential_scope_unsupported", 403)
        if key.type in {"ingest", "coding_agent"}:
            grant = (
                None
                if scope is None
                else AttachmentWriteGrant.objects.filter(
                    scope=scope,
                    api_key=key,
                    enabled=True,
                    expires_at__gt=timezone.now(),
                )
                .select_related("granted_by")
                .first()
            )
            if (
                grant is None
                or not grant.granted_by.is_active
                or not grant.granted_by.can_write_to_workspace(workspace)
            ):
                raise AttachmentError("credential_scope_unsupported", 403)
            if key.type == "coding_agent" and (
                not key.minted_by
                or not key.minted_by.is_active
                or not key.minted_by.can_write_to_workspace(workspace)
            ):
                raise AttachmentError("credential_scope_unsupported", 403)
        elif key.type == "user":
            if (
                not key.user
                or not key.user.is_active
                or not key.user.can_write_to_workspace(workspace)
            ):
                raise AttachmentError("permission_denied", 403)
        elif key.type != "system":
            raise AttachmentError("credential_scope_unsupported", 403)
    elif writer.kind == "user":
        user = User.objects.filter(pk=writer.id, is_active=True).first()
        if user is None or not user.can_write_to_workspace(workspace):
            raise AttachmentError("permission_denied", 403)
    else:
        raise AttachmentError("credential_scope_unsupported", 403)


def _creator_user_id(writer):
    """The user ingestion would record as a new project's creator, if any."""
    if writer.kind == "user":
        return writer.id
    key = OrgApiKey.no_workspace_objects.filter(
        pk=writer.id, organization_id=writer.organization_id
    ).first()
    if key is None:
        return None
    if key.type == "user":
        return key.user_id
    if key.type == "coding_agent":
        return key.minted_by_id
    first = (
        User.objects.filter(organization_id=writer.organization_id, is_active=True)
        .order_by("created_at")
        .values_list("pk", flat=True)
        .first()
    )
    return first


def resolve_scope(writer, *, project_name, project_type):
    """Ingestion's exact name/type/workspace project resolution, never a UUID.

    The writer's workspace was resolved by authentication: an invalid explicit
    workspace never falls back to a default. ``project_type`` is checked before
    ingestion's helper, which would otherwise coerce unknown types to experiment.
    A missing project is created exactly as an OTLP export would create it, and
    a missing scope as a disabled row, but only after the writer is shown to be
    eligible to write here. A refused writer leaves no Project or scope row.
    """
    from tracer.services.simulation_project import agent_id_in_simulation_project_name
    from tracer.utils.otel import get_or_create_project

    if (
        project_type not in {value for value, _ in PROJECT_TYPES}
        or not isinstance(project_name, str)
        or not 1 <= len(project_name) <= 255
        or agent_id_in_simulation_project_name(project_name) is not None
    ):
        raise AttachmentError("scope_unresolved", 404)
    workspace = Workspace.objects.filter(
        pk=writer.workspace_id,
        organization_id=writer.organization_id,
        is_active=True,
    ).first()
    if workspace is None:
        raise AttachmentError("scope_unresolved", 404)
    lookup = {
        "organization_id": writer.organization_id,
        "workspace_id": writer.workspace_id,
        "name": project_name,
        "trace_type": project_type,
    }
    project = Project.no_workspace_objects.filter(**lookup, deleted=False).first()
    if project is None and Project.no_workspace_objects.filter(**lookup).exists():
        # A deleted project with this identity is never adopted or revived.
        raise AttachmentError("scope_unresolved", 404)
    scope = (
        AttachmentScope.objects.filter(project=project).first()
        if project is not None
        else None
    )
    if scope is not None:
        check_writer(writer, scope)
        return scope
    # Nothing is created for a writer that may not write here.
    _check_identity(writer, workspace)
    with transaction.atomic():
        # One unit: a later denial rolls back the project and the scope row.
        if project is None:
            creator = _creator_user_id(writer)
            if creator is None:
                raise AttachmentError("scope_unresolved", 404)
            try:
                project = get_or_create_project(
                    project_name,
                    str(writer.organization_id),
                    project_type,
                    str(creator),
                    workspace_id=str(writer.workspace_id),
                )
            except Exception:  # noqa: BLE001 - project resolution boundary; fails closed as 404
                raise AttachmentError("scope_unresolved", 404) from None
            if project is None or project.deleted:
                raise AttachmentError("scope_unresolved", 404)
        scope, _ = AttachmentScope.objects.get_or_create(
            project=project,
            defaults={
                "organization_id": writer.organization_id,
                "workspace_id": writer.workspace_id,
            },
        )
        check_writer(writer, scope)
    return scope


def scope_document(scope):
    return {
        "organization_id": str(scope.organization_id),
        "workspace_id": str(scope.workspace_id),
        "project_id": str(scope.project_id),
    }


def _live_scope(scope):
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        raise AttachmentError("disabled", 403)
    if not scope.enabled:
        raise AttachmentError("disabled", 403)


def bootstrap(writer, body):
    """F01 ``bootstrapAttachmentSession``; ``body`` is a validated BootstrapRequest."""
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        raise AttachmentError("disabled", 403)
    if 1 not in body["schema_versions"]:
        raise AttachmentError("unsupported_version")
    scope = resolve_scope(
        writer, project_name=body["project_name"], project_type=body["project_type"]
    )
    client_id = UUID(body["capture_session_id"])
    nonce = UUID(body["correlation_nonce"])
    request_fingerprint = fingerprint(
        {
            "writer_kind": writer.kind,
            "writer_id": writer.id,
            "capture_session_id": client_id,
            "correlation_nonce": nonce,
        }
    )
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
        check_writer(writer, scope)
        _live_scope(scope)
        now = timezone.now()
        expires_at = now + timedelta(seconds=SESSION_SECONDS)
        session = (
            CaptureSession.objects.select_for_update()
            .filter(scope=scope, client_id=client_id)
            .first()
        )
        if session is None:
            if CaptureSession.objects.filter(
                scope=scope, correlation_nonce=nonce
            ).exists():
                raise AttachmentError("idempotency_conflict", 409)
            policy = server_policy(scope)
            try:
                with transaction.atomic():
                    session = CaptureSession.objects.create(
                        scope=scope,
                        client_id=client_id,
                        correlation_nonce=nonce,
                        writer_kind=writer.kind,
                        writer_id=writer.id,
                        capture_policy=asdict(policy),
                        policy_version=scope.policy_version,
                        deletion_generation=scope.deletion_generation,
                        expires_at=expires_at,
                        request_fingerprint=request_fingerprint,
                    )
            except IntegrityError:
                raise AttachmentError("idempotency_conflict", 409) from None
        else:
            # Renewal (BR-2): same writer, same pair, live and on current policy.
            if session.request_fingerprint != request_fingerprint:
                raise AttachmentError("idempotency_conflict", 409)
            if (
                session.revoked_at
                or session.expires_at <= now
                or session.policy_version != scope.policy_version
                or session.deletion_generation != scope.deletion_generation
            ):
                raise AttachmentError("expired", 409)
            session.expires_at = expires_at
            session.save(update_fields=["expires_at"])
    return session, {
        "schema_version": 1,
        "scope": scope_document(scope),
        "capture_session_id": str(session.client_id),
        "policy": policy_document(scope),
        "limits": dict(LIMITS),
        "expires_at": session.expires_at.isoformat(),
        "transfer_modes": ["authenticated_stream"],
    }


def check_session(session):
    scope = AttachmentScope.objects.get(pk=session.scope_id)
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False) or not scope.enabled:
        raise AttachmentError("disabled", 403)
    if (
        session.revoked_at
        or session.expires_at <= timezone.now()
        or session.policy_version != scope.policy_version
        or session.deletion_generation != scope.deletion_generation
    ):
        raise AttachmentError("capability_expired", 401)
    check_writer(
        Writer(
            session.writer_kind,
            session.writer_id,
            scope.organization_id,
            scope.workspace_id,
        ),
        scope,
    )
    return scope


def find_session(writer, scope, association):
    """The writer's own live capture session named by an F01 ``Association``."""
    session = CaptureSession.objects.filter(
        scope=scope,
        client_id=UUID(association["capture_session_id"]),
        correlation_nonce=UUID(association["correlation_nonce"]),
        writer_kind=writer.kind,
        writer_id=writer.id,
    ).first()
    if session is None:
        raise AttachmentError("correlation_unavailable", 403)
    if (
        session.revoked_at
        or session.expires_at <= timezone.now()
        or session.policy_version != scope.policy_version
        or session.deletion_generation != scope.deletion_generation
    ):
        raise AttachmentError("expired", 409)
    return session


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(payload):
    return salted_hmac(
        CAPABILITY_SALT, payload, secret=settings.SECRET_KEY, algorithm="sha256"
    ).digest()


def capability(session, upload, *, expires_at):
    """F01 ``write_capability``: ``fiac1.<claims>.<mac>`` (``^[a-zA-Z0-9_.-]+$``).

    The claims are exactly F01 ``WriteCapabilityClaims``; a fresh mint always has
    a new ``expires_at`` and therefore a different token (BR-1).
    """
    scope = AttachmentScope.objects.get(pk=session.scope_id)
    occurrence = upload.occurrence
    claims = {
        "audience": "fi-attachment-write",
        "actor_id": str(session.writer_id),
        "scope": scope_document(scope),
        "association": {
            "capture_session_id": str(session.client_id),
            "correlation_nonce": str(session.correlation_nonce),
            "parent": {
                "trace_id": str(occurrence.trace_id),
                "span_id": occurrence.span_id,
            },
        },
        "upload_id": str(upload.id),
        "attachment_id": str(upload.attachment.client_id),
        "occurrence_id": str(occurrence.client_id),
        "policy_version": str(upload.policy_version),
        "deletion_generation": upload.deletion_generation,
        "reserved_bytes": upload.expected_bytes,
        "operations": list(WRITE_OPERATIONS),
        "expires_at": expires_at.isoformat(),
    }
    if upload.recording_id:
        claims["recording_id"] = str(upload.recording.client_id)
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{CAPABILITY_PREFIX}.{payload}.{_b64(_signature(payload))}"


def is_capability(token):
    return isinstance(token, str) and token.startswith(CAPABILITY_PREFIX + ".")


def authorize(token, *, operation, scope_id, upload_id=None, recording_id=None):
    """Verify a write capability for exactly this scope, job and operation.

    Unknown, forged, foreign and expired-by-signature tokens are all 404 so the
    capability cannot probe other jobs. An expired capability is 401 ``expired``
    so the SDK renews once (SDK design 3.2).
    """
    try:
        prefix, payload, mac = token.split(".")
        if prefix != CAPABILITY_PREFIX or not hmac.compare_digest(
            _unb64(mac), _signature(payload)
        ):
            raise ValueError
        claims = json.loads(_unb64(payload))
        scope = AttachmentScope.objects.get(pk=scope_id)
        if (
            claims["audience"] != "fi-attachment-write"
            or operation not in claims["operations"]
            or claims["scope"] != scope_document(scope)
            or (upload_id is not None and claims["upload_id"] != str(upload_id))
            or (
                recording_id is not None
                and claims.get("recording_id") != str(recording_id)
            )
        ):
            raise ValueError
        session = CaptureSession.objects.get(
            scope_id=scope_id,
            client_id=UUID(claims["association"]["capture_session_id"]),
            correlation_nonce=UUID(claims["association"]["correlation_nonce"]),
            writer_id=UUID(claims["actor_id"]),
        )
        expires_at = datetime.fromisoformat(claims["expires_at"])
    except (
        AttributeError,
        AttachmentScope.DoesNotExist,
        CaptureSession.DoesNotExist,
        ValueError,
        TypeError,
        KeyError,
    ):
        raise AttachmentError("not_found", 404) from None
    if expires_at <= timezone.now():
        raise AttachmentError("capability_expired", 401)
    check_session(session)
    return session, claims


def session_policy(session):
    return CapturePolicy(**session.capture_policy)

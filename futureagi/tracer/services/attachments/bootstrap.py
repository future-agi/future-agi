"""Narrow broker, separate from APIKeyAuthentication and unrelated REST APIs.

The service takes an already-authenticated principal. It never authenticates an
ID supplied in a request body. Restricted credentials additionally require an
explicit, live, owner-managed AttachmentWriteGrant for this exact scope.
"""

from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from accounts.models import OrgApiKey, User
from accounts.models.workspace import Workspace
from tracer.models.attachment import (
    AttachmentScope,
    AttachmentWriteGrant,
    CaptureSession,
)
from tracer.models.project import Project
from tracer.services.attachments.contract import (
    AttachmentError,
    CapturePolicy,
    fingerprint,
    span_id,
    trace_uuid,
)

CAPABILITY_SALT = "fi.attachments.v1.write"
CAPABILITY_SECONDS = 300


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
        raise AttachmentError("scope_unresolved", 403)
    if not Project.no_workspace_objects.filter(
        pk=scope.project_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        deleted=False,
    ).exists():
        raise AttachmentError("not_found", 404)
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
                AttachmentWriteGrant.objects.filter(
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
                or not grant.granted_by.can_write_to_workspace(scope.workspace)
            ):
                raise AttachmentError("credential_scope_unsupported", 403)
            if key.type == "coding_agent" and (
                not key.minted_by
                or not key.minted_by.is_active
                or not key.minted_by.can_write_to_workspace(scope.workspace)
            ):
                raise AttachmentError("credential_scope_unsupported", 403)
        elif key.type == "user":
            if (
                not key.user
                or not key.user.is_active
                or not key.user.can_write_to_workspace(scope.workspace)
            ):
                raise AttachmentError("permission_denied", 403)
        elif key.type != "system":
            raise AttachmentError("credential_scope_unsupported", 403)
    elif writer.kind == "user":
        user = User.objects.filter(pk=writer.id, is_active=True).first()
        if user is None or not user.can_write_to_workspace(scope.workspace):
            raise AttachmentError("permission_denied", 403)
    else:
        raise AttachmentError("credential_scope_unsupported", 403)


def resolve_scope(writer, *, project_name, project_type):
    """Do not adopt null-workspace projects or route reserved names approximately.

    Existing projects only until the ingestion owner supplies the shared project
    creation/routing adapter. This intentionally fails closed rather than grant a
    restricted key new project-creation authority.
    """
    if (
        project_type not in {"observe", "experiment"}
        or not isinstance(project_name, str)
        or not 1 <= len(project_name) <= 255
    ):
        raise AttachmentError("scope_unresolved", 403)
    project = Project.no_workspace_objects.filter(
        organization_id=writer.organization_id,
        workspace_id=writer.workspace_id,
        name=project_name,
        trace_type=project_type,
        deleted=False,
    ).first()
    if project is None:
        raise AttachmentError("scope_unresolved", 403)
    scope, _ = AttachmentScope.objects.get_or_create(
        project=project,
        defaults={
            "organization_id": writer.organization_id,
            "workspace_id": writer.workspace_id,
        },
    )
    check_writer(writer, scope)
    return scope


@transaction.atomic
def bootstrap(writer, *, scope_id, client_id, trace_id, parent_span_id, policy):
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        raise AttachmentError("disabled", 403)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    check_writer(writer, scope)
    policy.validate()
    if not scope.enabled or not policy.enabled:
        raise AttachmentError("disabled", 403)
    trace_id, parent_span_id = trace_uuid(trace_id), span_id(parent_span_id)
    normalized = {
        "trace_id": trace_id,
        "span_id": parent_span_id,
        "policy": asdict(policy),
        "policy_version": scope.policy_version,
        "generation": scope.deletion_generation,
    }
    request_fingerprint = fingerprint(normalized)
    session, created = CaptureSession.objects.get_or_create(
        scope=scope,
        writer_kind=writer.kind,
        writer_id=writer.id,
        client_id=client_id,
        defaults={
            "trace_id": trace_id,
            "span_id": parent_span_id,
            "capture_policy": asdict(policy),
            "policy_version": scope.policy_version,
            "deletion_generation": scope.deletion_generation,
            "expires_at": timezone.now() + timedelta(seconds=CAPABILITY_SECONDS),
            "request_fingerprint": request_fingerprint,
        },
    )
    if not created and session.request_fingerprint != request_fingerprint:
        raise AttachmentError("idempotency_conflict", 409)
    check_session(session)
    return session, capability(session, "create")


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
        raise AttachmentError("capability_expired", 403)
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


def capability(session, operation, upload=None):
    if operation not in {"create", "transfer"}:
        raise AttachmentError("permission_denied", 403)
    return signing.dumps(
        {
            "session": str(session.id),
            "scope": str(session.scope_id),
            "operation": operation,
            "upload": str(upload.id) if upload else None,
        },
        salt=CAPABILITY_SALT,
        compress=False,
    )


def authorize(token, *, operation, scope_id, upload_id=None):
    try:
        claims = signing.loads(token, salt=CAPABILITY_SALT, max_age=CAPABILITY_SECONDS)
        allowed = (
            {"create"}
            if claims["operation"] == "create"
            else {"upload", "complete", "abort", "status"}
        )
        if (
            operation not in allowed
            or claims["scope"] != str(scope_id)
            or claims["upload"] != (str(upload_id) if upload_id else None)
        ):
            raise ValueError
        session = CaptureSession.objects.get(pk=claims["session"], scope_id=scope_id)
    except (
        signing.BadSignature,
        CaptureSession.DoesNotExist,
        ValueError,
        TypeError,
        KeyError,
    ):
        raise AttachmentError("not_found", 404) from None
    check_session(session)
    return session


def session_policy(session):
    return CapturePolicy(**session.capture_policy)

"""Attachment-specific authentication; never the generic API-key class.

Two credential classes, one per F01 ``x-authority``:

* The eligible ORIGINAL identity (``bootstrap``, ``initialize``, ``imports``,
  and every viewer route): an application JWT, a user/system API key, or an
  ingest/coding-agent key. Restricted keys are only identified here; their
  attachment-write eligibility is decided by ``bootstrap.check_writer``
  against an explicit ``AttachmentWriteGrant``. ``APIKeyAuthentication`` is
  not used because it refuses ingest keys outright and reads every bearer
  value as a JWT.
* The upload-bound WRITE CAPABILITY (content, complete, abort, status,
  manifest), recognised by its ``fiac1.`` prefix and verified in
  ``bootstrap.authorize``. A capability is never accepted as an identity and an
  identity is never accepted as a capability.

Viewer routes additionally require an application session (a JWT), because
playback grants are bound to that session and die with it.
"""

from dataclasses import dataclass
from uuid import UUID

from accounts.models import OrgApiKey
from accounts.models.workspace import Workspace
from tracer.models.attachment import AttachmentScope
from tracer.models.project import Project
from tracer.services.attachments.bootstrap import Writer, is_capability
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.occurrences import Viewer

MAX_CREDENTIAL_BYTES = 8 * 1024


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" or "api_key"
    id: UUID
    organization_id: UUID | None
    user: object | None = None
    api_key: OrgApiKey | None = None
    application_session_id: UUID | None = None
    saml_organization_id: UUID | None = None


def _header(request, name):
    value = request.headers.get(name)
    if value is None:
        return None
    if len(value) > MAX_CREDENTIAL_BYTES or "\r" in value or "\n" in value:
        raise AttachmentError("unauthenticated", 401)
    return value


def bearer(request):
    value = _header(request, "Authorization")
    if value is None:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token.strip():
        raise AttachmentError("unauthenticated", 401)
    return token.strip()


def capability_token(request):
    """The write capability. Original credentials must not ride along."""
    token = bearer(request)
    if not is_capability(token) or request.headers.get("X-Api-Key"):
        raise AttachmentError("unauthenticated", 401)
    return token


def identity(request):
    """Authenticate the original identity. Never accepts a write capability."""
    from accounts.authentication import decode_token, decrypt_message

    token = bearer(request)
    if token is not None:
        if is_capability(token):
            raise AttachmentError("unauthenticated", 401)
        try:
            user, token, auth_scope = decode_token(token)
            session_id = UUID(str(decrypt_message(token)["id"]))
        except Exception:
            # decode_token raises several exception types for bad, expired,
            # revoked and SAML-invalid tokens; none may leak a detail here.
            raise AttachmentError("unauthenticated", 401) from None
        if not user.is_active:
            raise AttachmentError("unauthenticated", 401)
        return Principal(
            kind="user",
            id=user.pk,
            organization_id=None,
            user=user,
            application_session_id=session_id,
            saml_organization_id=getattr(auth_scope, "org_id", None),
        )
    api_key = _header(request, "X-Api-Key")
    secret_key = _header(request, "X-Secret-Key")
    if not api_key or not secret_key:
        raise AttachmentError("unauthenticated", 401)
    key = (
        OrgApiKey.no_workspace_objects.select_related("organization")
        .filter(api_key=api_key, secret_key=secret_key, enabled=True, deleted=False)
        .first()
    )
    if key is None or key.organization_id is None:
        raise AttachmentError("unauthenticated", 401)
    return Principal(
        kind="api_key", id=key.pk, organization_id=key.organization_id, api_key=key
    )


def _user_org(request, principal):
    """The organization a JWT acts in: SAML-bound, explicit, or current."""
    from accounts.authentication import APIKeyAuthentication

    user = principal.user
    requested = request.headers.get("X-Organization-Id")
    if principal.saml_organization_id is not None:
        if requested and str(requested) != str(principal.saml_organization_id):
            raise AttachmentError("scope_unresolved", 404)
        return principal.saml_organization_id
    organization = APIKeyAuthentication()._resolve_organization(request, user)
    if organization is None or not user.can_access_organization(organization):
        raise AttachmentError("scope_unresolved", 404)
    return organization.pk


def bootstrap_writer(request, principal, body):
    """Writer for ``POST /attachment-sessions``: explicit workspace or default.

    An explicit workspace (body or ``X-Workspace-Id``) that is invalid, inactive,
    foreign or inaccessible fails ``scope_unresolved``; it never falls back.
    """
    explicit = body.get("workspace_id") or request.headers.get("X-Workspace-Id")
    if (
        body.get("workspace_id")
        and request.headers.get("X-Workspace-Id")
        and str(body["workspace_id"]) != request.headers.get("X-Workspace-Id")
    ):
        raise AttachmentError("scope_unresolved", 404)
    if principal.kind == "api_key":
        organization_id = principal.organization_id
        key_workspace = principal.api_key.workspace_id
    else:
        organization_id = _user_org(request, principal)
        key_workspace = None
    if explicit:
        try:
            workspace_id = UUID(str(explicit))
        except ValueError:
            raise AttachmentError("scope_unresolved", 404) from None
        workspace = Workspace.objects.filter(
            pk=workspace_id, organization_id=organization_id, is_active=True
        ).first()
    elif key_workspace:
        workspace = Workspace.objects.filter(
            pk=key_workspace, organization_id=organization_id, is_active=True
        ).first()
    else:
        workspace = Workspace.objects.filter(
            organization_id=organization_id, is_default=True, is_active=True
        ).first()
    if workspace is None or (key_workspace and workspace.pk != key_workspace):
        raise AttachmentError("scope_unresolved", 404)
    if principal.kind == "user" and not principal.user.can_access_workspace(workspace):
        raise AttachmentError("scope_unresolved", 404)
    return Writer(principal.kind, principal.id, organization_id, workspace.pk)


def project_scope(project_id):
    try:
        project_id = UUID(str(project_id))
    except ValueError:
        raise AttachmentError("not_found", 404) from None
    project = (
        Project.no_workspace_objects.filter(pk=project_id, deleted=False)
        .only("id", "organization_id", "workspace_id")
        .first()
    )
    if project is None or project.workspace_id is None:
        raise AttachmentError("not_found", 404)
    return project


def project_writer(principal, project):
    """Writer for a project-scoped write; scope eligibility is ``check_writer``."""
    if principal.kind == "api_key":
        key = principal.api_key
        if key.organization_id != project.organization_id or (
            key.workspace_id and key.workspace_id != project.workspace_id
        ):
            raise AttachmentError("not_found", 404)
    else:
        if principal.saml_organization_id is not None and str(
            principal.saml_organization_id
        ) != str(project.organization_id):
            raise AttachmentError("not_found", 404)
        workspace = Workspace.objects.filter(
            pk=project.workspace_id, organization_id=project.organization_id
        ).first()
        # Without any access the project is indistinguishable from a missing
        # one; a member without write access gets an eligibility denial.
        if workspace is None or not principal.user.can_access_workspace(workspace):
            raise AttachmentError("not_found", 404)
    scope = AttachmentScope.objects.filter(project_id=project.pk).first()
    if scope is None:
        raise AttachmentError("not_found", 404)
    return Writer(principal.kind, principal.id, project.organization_id, project.workspace_id), scope


def project_viewer(principal, project):
    """Viewer for scoped application routes: a live application session only."""
    if principal.kind != "user" or principal.application_session_id is None:
        raise AttachmentError("credential_scope_unsupported", 403)
    if principal.saml_organization_id is not None and str(
        principal.saml_organization_id
    ) != str(project.organization_id):
        raise AttachmentError("not_found", 404)
    return Viewer(
        principal.id,
        principal.application_session_id,
        project.organization_id,
        project.workspace_id,
        project.pk,
    )


def media_viewer(request, principal):
    """Viewer for the media-session exchange, which carries no project."""
    if principal.kind != "user" or principal.application_session_id is None:
        raise AttachmentError("credential_scope_unsupported", 403)
    organization_id = _user_org(request, principal)
    return Viewer(
        principal.id, principal.application_session_id, organization_id, None, None
    )

"""Admission for gateway-originated eval requests; no cached eligibility."""

from django.db.models import Q
from django.utils import timezone

from accounts.models.service_grant import ServiceGrant
from accounts.services.envelope_keys import pair_keys
from accounts.services.envelope_principals import live_person
from accounts.services.envelope_store import DurableNonceStore
from agentcc.models.api_key import AgentccAPIKey
from agentcc.services.key_revocation_authority import (
    RevocationAuthorityUnavailable,
    credential_is_revoked,
)
from tfc.routers import primary_required
from tfc.service_envelope import EnvelopeRejected, verify_grant, verify_request

EVAL_ROUTE = "/sdk/api/v1/eval/"


@primary_required
def authenticate_eval(request):
    if (
        request.path != EVAL_ROUTE
        or request.method != "POST"
        or request.META.get("QUERY_STRING", "")
    ):
        raise EnvelopeRejected()
    grant = request.headers.get("X-FA-Grant")
    claims = verify_request(
        request.headers.get("X-FA-Envelope"),
        keys=pair_keys("gateway", "fa-eval"),
        audience="fa-eval",
        method=request.method,
        route=request.path,
        body=request.body,
        nonces=DurableNonceStore(),
        grant=grant,
    )
    if claims["via"].startswith("key:"):
        key = (
            AgentccAPIKey._base_manager.filter(
                gateway_key_id=claims["via"][4:],
                organization_id=claims["org"],
                user_id=claims["user"],
                status=AgentccAPIKey.ACTIVE,
                deleted=False,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .first()
        )
        if key is None:
            raise EnvelopeRejected()
        try:
            if credential_is_revoked(key.gateway_key_id, key.key_hash):
                raise EnvelopeRejected()
        except RevocationAuthorityUnavailable:
            raise EnvelopeRejected() from None
    else:
        row = ServiceGrant.objects.filter(pk=claims["via"][6:]).first()
        delegated = verify_grant(
            grant,
            keys=pair_keys("fa", "fa-eval-grant"),
            audience="fa-eval-grant",
            row=row.registry_row() if row else None,
        )
        if any(delegated[f] != claims[f] for f in ("org", "workspace", "user")):
            raise EnvelopeRejected()
    user, org, workspace = live_person(
        claims["org"], claims["user"], claims["workspace"]
    )
    _bind_context(request, user, org, workspace)
    request.credential_provenance = "service_envelope"
    request.service_envelope = claims
    return user, None


def _bind_context(request, user, org, workspace):
    from accounts.authentication import APIKeyAuthentication
    from tfc.middleware.workspace_context import set_workspace_context

    workspace = workspace or _existing_workspace(user, org)
    # Auth consumes authority; it cannot provision a default or a membership.
    # No workspace must not accidentally turn a workspace write into an org write.
    if workspace is None or not user.can_write_to_workspace(workspace):
        raise EnvelopeRejected()
    user.organization = org
    APIKeyAuthentication()._bind_user_context(user)
    request.organization = org
    request.workspace = workspace
    set_workspace_context(workspace=workspace, organization=org, user=user)


def _existing_workspace(user, org):
    from accounts.models import Workspace
    from accounts.models.workspace import WorkspaceMembership

    workspaces = Workspace._base_manager.filter(
        organization=org, is_active=True, deleted=False
    )
    memberships = WorkspaceMembership._base_manager.filter(
        user=user,
        is_active=True,
        deleted=False,
        workspace__organization=org,
        workspace__is_active=True,
        workspace__deleted=False,
    )
    if not user.has_global_workspace_access(org):
        workspaces = workspaces.filter(pk__in=memberships.values("workspace_id"))
    config = user.config if isinstance(user.config, dict) else {}
    workspace_map = config.get("orgWorkspaceMap")
    workspace_map = workspace_map if isinstance(workspace_map, dict) else {}
    preferred = workspace_map.get(str(org.pk)) or (
        config.get("currentWorkspaceId") or config.get("defaultWorkspaceId")
    )
    if preferred:
        from django.core.exceptions import ValidationError

        try:
            workspace = workspaces.filter(pk=preferred).first()
        except (ValueError, ValidationError):
            workspace = None
        if workspace:
            return workspace
    membership = memberships.order_by("pk").first()
    if membership:
        return workspaces.filter(pk=membership.workspace_id).first()
    return workspaces.filter(is_default=True).first()

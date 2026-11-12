"""Which gateway resource a token is for, and in which organizations a person
may ask for it (RFC 8707).

A resource is one of the configured gateway MCP endpoints (``https://<gw>/mcp``,
which serves the org named by the token) or one virtual server under it,
addressed as the gateway addresses it (decision 32,
tenant.OrgConfig.MCPVirtualServer): by its id (``/mcp/v/vs_...``) or by its
slug (``/mcp/v/<slug>``), the URL the console hands out. An id belongs to one
organization; a slug is unique only within an organization, and the token's
``org_id`` says which one. Either way a person can ask for it only in an
organization whose active config holds it, and only while they are an active
member of that organization.
"""

import re
from dataclasses import dataclass

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from agentcc.mcp_oauth import config
from agentcc.models.org_config import AgentccOrgConfig

VS_ID = re.compile(r"^vs_[0-9A-HJKMNP-TV-Z]{26}$")
VS_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class Resource:
    uri: str
    # The virtual server's id or slug as the URL names it; None for /mcp.
    virtual_server: str | None


def parse(value) -> Resource | None:
    """The resource ``value`` names, or None when it is not a gateway MCP
    endpoint this server issues tokens for. The match is exact: no query, no
    fragment, no trailing slash, no other path."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    for base in config.resource_bases():
        if value == base:
            return Resource(uri=value, virtual_server=None)
        prefix = base + "/v/"
        ref = value[len(prefix) :] if value.startswith(prefix) else ""
        if VS_ID.fullmatch(ref) or VS_SLUG.fullmatch(ref):
            return Resource(uri=value, virtual_server=ref)
    return None


def member_org_ids(user) -> list:
    """The organizations ``user`` is an active member of right now."""
    if user is None or not getattr(user, "is_active", False):
        return []
    return list(
        OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).values_list("organization_id", flat=True)
    )


def _holds_virtual_server(mcp, ref) -> bool:
    """Whether the config holds the virtual server ``ref`` names: its id, or
    its slug, as the gateway resolves it."""
    servers = mcp.get("virtual_servers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict):
        return False
    if VS_ID.fullmatch(ref):
        return any(
            isinstance(entry, dict) and entry.get("id") == ref
            for entry in servers.values()
        )
    return isinstance(servers.get(ref), dict)


def organizations_for(user, resource: Resource) -> list[Organization]:
    """The organizations in which ``user`` may be granted ``resource``: every
    org they belong to for the MCP endpoint itself, only the virtual server's
    own org for a virtual server."""
    org_ids = member_org_ids(user)
    if not org_ids:
        return []
    if resource.virtual_server is not None:
        configs = AgentccOrgConfig.no_workspace_objects.filter(
            organization_id__in=org_ids, is_active=True
        ).only("organization_id", "mcp")
        org_ids = [
            c.organization_id
            for c in configs
            if _holds_virtual_server(c.mcp, resource.virtual_server)
        ]
        if not org_ids:
            return []
    return list(Organization.objects.filter(id__in=org_ids).order_by("name", "id"))


def user_may_use(user, organization_id, resource_uri) -> bool:
    """Whether ``user`` may still hold a token for ``resource_uri`` in that
    org: re-checked at every code exchange and refresh."""
    resource = parse(resource_uri)
    if resource is None:
        return False
    return any(
        str(org.id) == str(organization_id) for org in organizations_for(user, resource)
    )

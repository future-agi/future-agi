"""MCP admin reads and the tool playground, for one org.

Every answer is about the org's own servers, the ``mcp.servers`` of its active
config: anything the gateway returns that is not attributed to one of those
servers is left out.

A gateway that runs with MCP off registers no ``/-/mcp/*`` route and answers
404. The status then reads as disabled with ``reason`` ``gateway_mcp_disabled``
for every org, and a list read or the playground raises ``MCPDisabled``. Any
other gateway failure stays an error: ``gateway_unreachable`` on the status,
``GatewayClientError`` from a list read or the playground.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.gateway_admin import mcp as gateway_mcp
from agentcc.services.gateway_client import GatewayClientError

if TYPE_CHECKING:
    from accounts.models import Organization

logger = structlog.get_logger(__name__)

MCP_TOOL_NOT_FOUND = "mcp_tool_not_found"
MCP_TOOL_NOT_FOUND_MESSAGE = "MCP tool not found."
NO_MCP_SERVERS_MESSAGE = "No MCP servers configured for this org"
GATEWAY_MCP_DISABLED = "gateway_mcp_disabled"
GATEWAY_MCP_DISABLED_MESSAGE = "MCP is turned off for this deployment."
GATEWAY_UNREACHABLE = "gateway_unreachable"
GATEWAY_UNREACHABLE_MESSAGE = "The gateway could not be reached for MCP data."


class MCPToolNotFound(Exception):
    """The tool is unknown, or its server is not one of the org's.

    Both get the same answer, so a caller learns nothing about other orgs.
    """


class NoMCPServers(Exception):
    """The org has no MCP servers configured."""


class MCPDisabled(Exception):
    """The deployment runs with MCP off: the gateway has no MCP admin routes."""


def org_server_ids(organization: "Organization | None") -> frozenset[str]:
    """The ids of the org's MCP servers, from its active config.

    A stored ``mcp`` or ``servers`` value that is not an object (a legacy or
    hand-edited row) counts as no servers.
    """
    if organization is None:
        return frozenset()
    config = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization, is_active=True, deleted=False
        )
        .only("mcp")
        .first()
    )
    mcp = config.mcp if config else None
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    return frozenset(servers) if isinstance(servers, dict) else frozenset()


def org_status(organization: "Organization | None") -> dict[str, Any]:
    """The org's MCP status: ``enabled``, its own servers and its own counts.

    When the gateway answers 404 (its MCP is off) or cannot be reached, the
    status is disabled with ``reason`` ``gateway_mcp_disabled`` or
    ``gateway_unreachable``, and lists no server. The gateway is asked even for
    an org with no MCP server, so that org also learns that MCP is off; with
    MCP on, such an org reads as disabled with no reason.
    """
    if organization is None:
        return _disabled_status()
    servers = org_server_ids(organization)
    org_id = str(organization.id)
    try:
        status = gateway_mcp.get_status(org_id)
    except GatewayClientError as e:
        reason = GATEWAY_MCP_DISABLED if _is_mcp_off(e) else GATEWAY_UNREACHABLE
        logger.warning(
            "mcp_status_unavailable", org_id=org_id, reason=reason, error=str(e)
        )
        return {**_disabled_status(), "reason": reason}
    if not servers:
        return _disabled_status()

    own = [s for s in status["servers"] if s.get("server_id") in servers]
    enabled = status.get("enabled") is True and status.get("org_enabled") is not False
    resources = (
        _counted_items(gateway_mcp.list_resources, org_id, servers) if enabled else []
    )
    prompts = (
        _counted_items(gateway_mcp.list_prompts, org_id, servers) if enabled else []
    )
    return {
        "enabled": enabled,
        # Sessions belong to no server: only an org-scoped status counts this org's.
        "sessions": (
            _count(status.get("sessions"))
            if gateway_mcp.is_org_scoped_status(status)
            else 0
        ),
        "tools": sum(_count(s.get("tool_count")) for s in own),
        "resources": len(resources),
        "prompts": len(prompts),
        "servers": own,
    }


def org_tools(organization: "Organization | None") -> list[dict[str, Any]]:
    """The org's tools. Raises ``MCPDisabled`` or ``GatewayClientError``.

    ``org_resources`` and ``org_prompts`` answer and raise the same way; an org
    with no MCP server gets an empty list without a gateway call.
    """
    return _org_items(gateway_mcp.list_tools, organization)


def org_resources(organization: "Organization | None") -> list[dict[str, Any]]:
    return _org_items(gateway_mcp.list_resources, organization)


def org_prompts(organization: "Organization | None") -> list[dict[str, Any]]:
    return _org_items(gateway_mcp.list_prompts, organization)


def run_tool_test(
    organization: "Organization | None", name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Run one of the org's tools through the gateway's admin playground.

    Raises ``NoMCPServers``, ``MCPToolNotFound``, ``MCPDisabled`` or
    ``GatewayClientError``.
    """
    servers = org_server_ids(organization)
    if not servers:
        raise NoMCPServers(NO_MCP_SERVERS_MESSAGE)
    org_id = str(organization.id)
    # The tool's server is the one the gateway's tool list names. A name prefix
    # cannot tell, because a server id may contain the "_" separator.
    tools = _gateway_read(gateway_mcp.list_tools, org_id)
    owner = next((t["server_id"] for t in tools if t.get("name") == name), None)
    if owner not in servers:
        raise MCPToolNotFound(MCP_TOOL_NOT_FOUND_MESSAGE)
    return _gateway_read(
        lambda oid: gateway_mcp.run_tool_test(oid, name, arguments), org_id
    )


def _disabled_status() -> dict[str, Any]:
    return {
        "enabled": False,
        "sessions": 0,
        "tools": 0,
        "resources": 0,
        "prompts": 0,
        "servers": [],
    }


def _org_items(
    fetch: Callable[[str], list[dict[str, Any]]], organization: "Organization | None"
) -> list[dict[str, Any]]:
    servers = org_server_ids(organization)
    if not servers:
        return []
    items = _gateway_read(fetch, str(organization.id))
    return _own(items, servers)


def _counted_items(
    fetch: Callable[[str], list[dict[str, Any]]],
    org_id: str,
    servers: frozenset[str],
) -> list[dict[str, Any]]:
    """The org's items for a status count: none when the read fails.

    The status call is the one that reports an outage.
    """
    try:
        items = fetch(org_id)
    except GatewayClientError as e:
        logger.warning(
            "mcp_list_unavailable", org_id=org_id, fetch=fetch.__name__, error=str(e)
        )
        return []
    return _own(items, servers)


def _gateway_read(fetch: Callable[[str], Any], org_id: str) -> Any:
    """``fetch(org_id)``, raising ``MCPDisabled`` when the gateway's MCP is off."""
    try:
        return fetch(org_id)
    except GatewayClientError as e:
        if _is_mcp_off(e):
            raise MCPDisabled(GATEWAY_MCP_DISABLED_MESSAGE) from e
        raise


def _is_mcp_off(error: GatewayClientError) -> bool:
    # With MCP off the gateway registers no /-/mcp/* route: every one is 404.
    return error.status_code == 404


def _own(items: list[dict[str, Any]], servers: frozenset[str]) -> list[dict[str, Any]]:
    return [item for item in items if item["server_id"] in servers]


def _count(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else 0
    )

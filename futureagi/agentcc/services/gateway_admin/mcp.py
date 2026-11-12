"""Gateway admin calls for MCP (plan section 6.22, MC.7), with their response adapter.

Every call passes ``org_id`` (M9 makes the gateway answer from that org's
pool). The adapter reads the ``mcp-admin-status-v1`` and ``mcp-admin-tools-v1``
shapes and, until M9 merges, the shapes the gateway sends today:

- ``/-/mcp/tools``, ``/-/mcp/resources`` and ``/-/mcp/prompts`` answer a bare
  list; v1 wraps it as ``{"tools": [...]}`` (resources and prompts likewise).
- A tool names its server in ``server``; v1 names it ``server_id``. Resources
  and prompts carry no server today, so none of them can be attributed.

Each list item is returned with ``server_id`` set to its server, or ``None``
when the gateway did not attribute it. Every function raises
``GatewayClientError``: with the HTTP status for a status of 400 or more, and
with none for a transport failure or a body of the wrong shape.
"""

from typing import Any

import httpx
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import MCPAdminTools
from agentcc.services.gateway_admin.base import GatewayAdminClient
from agentcc.services.gateway_client import GatewayClientError

STATUS_PATH = "/-/mcp/status"
TOOLS_PATH = "/-/mcp/tools"
RESOURCES_PATH = "/-/mcp/resources"
PROMPTS_PATH = "/-/mcp/prompts"
TEST_PATH = "/-/mcp/test"


def get_status(org_id: str) -> dict[str, Any]:
    """The gateway's MCP status body; its ``servers`` items keep their fields."""
    body = _get(STATUS_PATH, org_id)
    if not isinstance(body, dict):
        raise GatewayClientError(f"{STATUS_PATH} returned a {type(body).__name__}")
    servers = body.get("servers") or []
    if not isinstance(servers, list):
        raise GatewayClientError(f"{STATUS_PATH} servers is not a list")
    return {**body, "servers": [s for s in servers if isinstance(s, dict)]}


def is_org_scoped_status(status: dict[str, Any]) -> bool:
    """Whether the status answers for one org's pool (v1 carries ``org_enabled``).

    The gateway at the PIN sends one registry's counts for every org.
    """
    return "org_enabled" in status


def list_tools(org_id: str) -> list[dict[str, Any]]:
    return _list(TOOLS_PATH, org_id, "tools")


def list_served_tools(
    org_id: str, vs: str = "", key_id: str = "", server_id: str = ""
) -> list[dict[str, Any]]:
    """Typed runtime list, not the legacy administrative inventory adapter."""
    body = _call(
        "GET",
        "/-/mcp/served-tools",
        params={"org_id": org_id, "vs": vs, "key_id": key_id, "server_id": server_id},
    )
    try:
        result = MCPAdminTools.model_validate(body)
    except ValidationError:
        # Validation errors may embed upstream metadata; do not log them.
        raise GatewayClientError("Invalid served-tool response") from None
    if any(tool.identity is None or tool.quarantined for tool in result.tools):
        raise GatewayClientError("Invalid served-tool identity or posture")
    # Explicit projection: this read never forwards annotations or raw metadata.
    return [
        tool.model_dump(
            include={
                "name",
                "server_id",
                "upstream_name",
                "revision",
                "quarantined",
                "identity",
            },
            exclude_none=True,
        )
        for tool in result.tools
    ]


def list_resources(org_id: str) -> list[dict[str, Any]]:
    return _list(RESOURCES_PATH, org_id, "resources")


def list_prompts(org_id: str) -> list[dict[str, Any]]:
    return _list(PROMPTS_PATH, org_id, "prompts")


def run_tool_test(org_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool through the gateway's admin playground."""
    body = _call(
        "POST",
        TEST_PATH,
        json={"org_id": org_id, "name": name, "arguments": arguments},
    )
    if not isinstance(body, dict):
        raise GatewayClientError(f"{TEST_PATH} returned a {type(body).__name__}")
    return body


def _get(path: str, org_id: str) -> Any:
    return _call("GET", path, params={"org_id": org_id})


def _call(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any = None,
) -> Any:
    try:
        return GatewayAdminClient().request(method, path, json=json, params=params)
    except httpx.HTTPError as e:
        # The transport maps only connect errors and timeouts itself; the rest
        # (a reset, a body that fails to decode, a redirect loop) land here.
        raise GatewayClientError(f"{path} transport error: {e}") from e
    except ValueError as e:
        # The transport decodes the body as JSON without a guard.
        raise GatewayClientError(f"{path} returned a body that is not JSON") from e


def _list(path: str, org_id: str, key: str) -> list[dict[str, Any]]:
    body = _get(path, org_id)
    items = body.get(key) if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise GatewayClientError(f"{path} returned no {key} list")
    return [
        {**item, "server_id": _server_id(item)}
        for item in items
        if isinstance(item, dict)
    ]


def _server_id(item: dict[str, Any]) -> str | None:
    # Every mcp-admin-*-v1 item carries ``server_id`` since M9's org-scoped
    # admin API (W3b); an item without one (the PIN gateway's ``server``
    # attribution) is not provably the org's, so it is dropped (M6 IR-6).
    value = item.get("server_id")
    return value if isinstance(value, str) and value else None

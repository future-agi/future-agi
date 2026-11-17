"""Gateway admin call for MCP tool revisions (M20's ``GET /-/mcp/revisions``).

The gateway answers ``{"revisions": [...]}``, each item an ``mcp-revision-v1``
record (``MCPRevision``): a tool of one of the org's own servers that pinning
holds or warns about, with its definition, the accepted one when the gateway
has listed it, and a field-level diff. It answers from that org's pool only.
"""

from typing import Any

from pydantic import ValidationError

from agentcc.contracts.gateway_admin import MCPRevision
from agentcc.services.gateway_admin.mcp import _call
from agentcc.services.gateway_client import GatewayClientError

REVISIONS_PATH = "/-/mcp/revisions"


def list_revisions(org_id: str) -> list[dict[str, Any]]:
    """The org's held tool revisions, each checked against ``MCPRevision``.

    Raises ``GatewayClientError``: with the HTTP status for a status of 400 or
    more (404 when the gateway serves no MCP or has no config for the org),
    and with none for a transport failure or a body of the wrong shape.
    """
    body = _call("GET", REVISIONS_PATH, params={"org_id": org_id})
    items = body.get("revisions") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise GatewayClientError(f"{REVISIONS_PATH} returned no revisions list")
    out = []
    for item in items:
        try:
            MCPRevision.model_validate(item)
        except ValidationError as e:
            raise GatewayClientError(
                f"{REVISIONS_PATH} returned a record that is not mcp-revision-v1: "
                f"{e.error_count()} errors"
            ) from None
        if item.get("org_id") != org_id:
            raise GatewayClientError(
                f"{REVISIONS_PATH} returned a record of another org"
            )
        out.append(item)
    return out

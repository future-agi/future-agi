"""Gateway admin call for per-user MCP tokens (M25's ``/-/mcp/credentials/purge``).

After a member connects, reconnects or disconnects, Django asks the gateway to
drop the token it cached for them and the upstream client it holds for them,
so the next call fetches afresh (``mcp-upstream-token-v1``). After a shared
static credential is revoked, lapses or is replaced, the same call names the
grant revision its org and server are revoked through, and every gateway
replica stops, and never again admits, a pushed grant at or below it. The call
never carries a token.
"""

import httpx
import structlog

from agentcc.services.gateway_admin.base import GatewayAdminClient
from agentcc.services.gateway_client import GatewayClientError

logger = structlog.get_logger(__name__)

PURGE_PATH = "/-/mcp/credentials/purge"


def purge(
    org_id: str,
    server_id: str,
    user_id: str | None = None,
    *,
    revision: int | None = None,
) -> bool:
    """Whether the gateway dropped what it cached (with ``revision``: whether
    it revoked the org's pushed grants of ``server_id`` through it). Never
    raises: a gateway that is down, or predates the route, keeps a cached
    token at most until the expiry it was given; the caller keeps the
    obligation and retries a failed revocation."""
    body = {"org_id": org_id, "server_id": server_id}
    if user_id:
        body["user_id"] = user_id
    if revision:
        body["revision"] = revision
    try:
        GatewayAdminClient().request("POST", PURGE_PATH, json=body)
    except (GatewayClientError, httpx.HTTPError, ValueError) as error:
        logger.warning(
            "mcp_credentials_purge_failed",
            org_id=org_id,
            server_id=server_id,
            status_code=getattr(error, "status_code", None),
            error_type=type(error).__name__,
        )
        return False
    return True

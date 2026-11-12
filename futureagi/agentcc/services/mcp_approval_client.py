"""Approval transport boundary; never automatically replay a decision POST."""

import ssl
from urllib.parse import quote

import httpx

from agentcc.services.gateway_client import GatewayClientError, get_gateway_client
from agentcc.services.mcp_admin import GATEWAY_MCP_DISABLED


class MCPApprovalsDisabled(GatewayClientError):
    """The gateway serves no approval route: its router answered with its own
    unknown-endpoint 404 (error code not_found). A gateway running with MCP
    off (the hosted profile requires it) registers no /-/mcp/* route, and one
    that predates the approval routes has none either. An approval's own 404
    (approval_not_found) never carries that code, so it is never this.

    It keeps the 404 status, so callers that map GatewayClientError statuses
    answer exactly as before."""

    reason = GATEWAY_MCP_DISABLED

    def __init__(self):
        super().__init__(
            "The gateway serves no approval routes.",
            status_code=404,
            error_code="not_found",
        )


class MCPApprovalClient:
    def __init__(self, client=None):
        self.client = client or get_gateway_client()

    def _request(self, *args, **kwargs):
        try:
            return self.client._request(*args, **kwargs)
        except GatewayClientError as exc:
            if exc.status_code == 404 and exc.error_code == "not_found":
                raise MCPApprovalsDisabled() from None
            # c26's shared client already normalizes some network failures.
            # Preserve refusal status but never expose its body, URL or cause.
            raise GatewayClientError(
                "Approval gateway transport failed.", status_code=exc.status_code or 503
            ) from None
        except (httpx.RequestError, httpx.InvalidURL, ssl.SSLError, ValueError):
            # Response loss may follow a committed decision. Do not disclose
            # URLs, credentials or unbounded transport text, and do not retry.
            raise GatewayClientError(
                "Approval gateway transport failed.", status_code=503
            ) from None

    def list(self, organization, cursor=""):
        params = {"org_id": str(organization.pk)}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/-/mcp/approvals", params=params)

    def get(self, organization, approval_id):
        return self._request(
            "GET",
            f"/-/mcp/approvals/{quote(approval_id, safe='')}",
            params={"org_id": str(organization.pk)},
        )

    def decide(self, organization, approval_id, decision, actor, note=""):
        return self._request(
            "POST",
            f"/-/mcp/approvals/{quote(approval_id, safe='')}/decision",
            json_body={
                "org_id": str(organization.pk),
                "decision": decision,
                "actor": str(actor.pk),
                "note": note,
            },
        )

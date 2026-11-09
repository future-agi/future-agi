"""Gateway admin call for the MCP connection probe (plan section 6.22, M17's
``POST /-/mcp/probe``), the Django side of the console's "Test connection"
button. M29's registry review reuses it.

The gateway never takes a ``credential_id``: a caller resolves a stored
credential from the vault and sends it inline as ``auth``, which the gateway
uses for that one probe and never stores or logs. Neither does this module:
an error names the path, the status and the gateway's own message, never the
request body.
"""

from typing import Any

import httpx
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import MCPProbeResult
from agentcc.services.gateway_admin.base import GatewayAdminClient
from agentcc.services.gateway_client import GatewayClientError

PROBE_PATH = "/-/mcp/probe"
# The gateway gives up on an upstream after 15 s (mcp/probe.go); the shared
# admin transport's 10 s would cut a slow but working probe short.
PROBE_TIMEOUT_SECONDS = 20.0


class ProbeRefused(GatewayClientError):
    """The gateway refused the probe request itself (400
    ``invalid_probe_request``): a malformed URL, transport or auth."""


def probe(
    org_id: str,
    url: str,
    *,
    transport: str | None = None,
    auth: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one probe; returns the gateway's ``mcp-probe-v1`` result.

    Raises ``ProbeRefused`` for the gateway's 400, and ``GatewayClientError``
    for any other failure or a body that is not an ``MCPProbeResult``.
    """
    body: dict[str, Any] = {"org_id": org_id, "url": url}
    if transport:
        body["transport"] = transport
    if auth:
        body["auth"] = auth
    response = _ProbeClient().post_probe(body)
    if response.status_code == 400:
        raise ProbeRefused(_gateway_message(response), status_code=400)
    if response.status_code >= 400:
        raise GatewayClientError(
            f"{PROBE_PATH} returned {response.status_code}",
            status_code=response.status_code,
        )
    try:
        result = response.json()
        MCPProbeResult.model_validate(result)
    except (ValueError, ValidationError) as e:
        # A ValidationError's text can quote the body; only its type is kept.
        raise GatewayClientError(
            f"{PROBE_PATH} returned a body that is not mcp-probe-v1 ({type(e).__name__})"
        ) from None
    return result


class _ProbeClient(GatewayAdminClient):
    """The admin client's base URL and admin token, with the probe's timeout."""

    def post_probe(self, body: dict[str, Any]) -> httpx.Response:
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS) as client:
                return client.request(
                    "POST",
                    f"{self.base_url}{PROBE_PATH}",
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException:
            raise GatewayClientError(f"{PROBE_PATH} timed out") from None
        except httpx.HTTPError as e:
            raise GatewayClientError(
                f"{PROBE_PATH} transport error ({type(e).__name__})"
            ) from None


def _gateway_message(response: httpx.Response) -> str:
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        message = None
    return message if isinstance(message, str) and message else "invalid probe request"

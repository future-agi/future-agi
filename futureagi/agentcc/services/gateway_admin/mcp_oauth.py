"""Gateway admin call that hands the gateway the authorization server's public
verification keys (``PUT /-/mcp/oauth/keys``, ``mcp-oauth-keys-push-v1``,
M23's endpoint).

Only public keys ever leave Django: a key carrying a private member is
refused here before anything is sent. An error names the path and the
status, never the body.
"""

from typing import Any

import httpx
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import MCPOAuthKeysPush
from agentcc.services.gateway_admin.base import GatewayAdminClient
from agentcc.services.gateway_client import GatewayClientError

KEYS_PATH = "/-/mcp/oauth/keys"
# A sign-in may trigger the push; it must never hang on a gateway that is down.
PUSH_TIMEOUT_SECONDS = 3.0
_PRIVATE_MEMBERS = ("d", "p", "q", "k")


def push_keys(payload: dict[str, Any]) -> None:
    """PUT the payload; raises ``GatewayClientError`` unless the gateway
    answers 2xx."""
    try:
        MCPOAuthKeysPush.model_validate(payload)
    except ValidationError:
        raise GatewayClientError(
            f"{KEYS_PATH}: the payload is not mcp-oauth-keys-push-v1"
        ) from None
    for key in payload["keys"]:
        if any(member in key for member in _PRIVATE_MEMBERS):
            raise GatewayClientError(f"{KEYS_PATH}: refusing to send a private key")
    response = _KeysClient().put_keys(payload)
    if response.status_code >= 300:
        raise GatewayClientError(
            f"{KEYS_PATH} returned {response.status_code}",
            status_code=response.status_code,
        )


class _KeysClient(GatewayAdminClient):
    def put_keys(self, body: dict[str, Any]) -> httpx.Response:
        try:
            with httpx.Client(timeout=PUSH_TIMEOUT_SECONDS) as client:
                return client.request(
                    "PUT",
                    f"{self.base_url}{KEYS_PATH}",
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException:
            raise GatewayClientError(f"{KEYS_PATH} timed out") from None
        except httpx.HTTPError as e:
            raise GatewayClientError(
                f"{KEYS_PATH} transport error ({type(e).__name__})"
            ) from None

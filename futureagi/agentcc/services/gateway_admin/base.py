"""Shared transport for gateway admin calls made by one concern module each.

New gateway admin endpoints get a module in this package (for example
``credits.py``) built on ``GatewayAdminClient`` instead of growing
``gateway_client.py``. The base URL, admin token, timeout and error mapping are
the ones ``gateway_client.GatewayClient`` already uses.
"""

from typing import Any

from agentcc.services import gateway_client


class GatewayAdminClient(gateway_client.GatewayClient):
    def __init__(self, base_url: str | None = None, admin_token: str | None = None):
        # Looked up per instance, so a patched gateway_client value applies.
        super().__init__(
            base_url=base_url or gateway_client.AGENTCC_GATEWAY_INTERNAL_URL,
            admin_token=(
                gateway_client.AGENTCC_ADMIN_TOKEN
                if admin_token is None
                else admin_token
            ),
        )

    def request(
        self,
        method: str,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call ``path`` on the gateway admin API and return the decoded JSON body.

        Raises ``GatewayClientError`` on a status of 400 or more, a connection
        failure or a timeout.
        """
        return self._request(method, path, params=params, json_body=json)

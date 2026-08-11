import json
import os

import httpx
import structlog

from agentcc.contracts.gateway_admin import (
    CreateKeyRequest,
    OrgConfig as GatewayOrgConfig,
    UpdateKeyRequest,
)

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 10.0


def resolve_gateway_public_url():
    return os.environ.get("AGENTCC_GATEWAY_URL", "http://localhost:8090")


def resolve_gateway_internal_url():
    # `AGENTCC_INTERNAL_URL` is the older docker-compose/.env key still used by
    # local stacks. Keep it as a compatibility fallback so container-to-container
    # gateway calls do not silently fall back to localhost inside the backend.
    return (
        os.environ.get("AGENTCC_GATEWAY_INTERNAL_URL")
        or os.environ.get("AGENTCC_INTERNAL_URL")
        or resolve_gateway_public_url()
    )


# Default gateway address — set via env vars in docker-compose / .env
AGENTCC_GATEWAY_URL = resolve_gateway_public_url()
# Internal URL for container-to-container communication (e.g. http://agentcc-gateway:8090)
AGENTCC_GATEWAY_INTERNAL_URL = resolve_gateway_internal_url()
AGENTCC_ADMIN_TOKEN = os.environ.get("AGENTCC_ADMIN_TOKEN", "")
if not AGENTCC_ADMIN_TOKEN:
    logger.warning(
        "AGENTCC_ADMIN_TOKEN not set — gateway admin API calls will be unauthenticated"
    )


class GatewayClientError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class GatewayClient:
    """HTTP client for communicating with the Agentcc Go gateway admin API."""

    def __init__(self, base_url, admin_token=""):
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        return headers

    def _request(self, method, path, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                resp = client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                )
            if resp.status_code >= 400:
                raise GatewayClientError(
                    f"Gateway returned {resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                )
            return resp.json() if resp.content else {}
        except httpx.ConnectError as e:
            raise GatewayClientError(f"Cannot connect to gateway at {url}: {e}") from e
        except httpx.TimeoutException as e:
            raise GatewayClientError(f"Gateway request timed out: {e}") from e

    # --- Health ---

    def health_check(self):
        return self._request("GET", "/healthz")

    def ready_check(self):
        return self._request("GET", "/readyz")

    def provider_health(self):
        return self._request("GET", "/-/health/providers")

    # --- Config ---

    def get_config(self):
        return self._request("GET", "/-/config")

    def reload_config(self):
        return self._request("POST", "/-/reload")

    # --- Keys ---

    def list_keys(self):
        return self._request("GET", "/-/keys")

    def create_key(self, name, owner="", models=None, providers=None, metadata=None):
        body = CreateKeyRequest(
            name=name,
            owner=owner or None,
            models=models or None,
            providers=providers or None,
            metadata=_stringify_metadata(metadata) if metadata else None,
        ).model_dump(exclude_none=True)
        return self._request("POST", "/-/keys", json_body=body)

    def get_key(self, key_id):
        return self._request("GET", f"/-/keys/{key_id}")

    def revoke_key(self, key_id):
        return self._request("DELETE", f"/-/keys/{key_id}")

    def update_key(self, key_id, **kwargs):
        body = {}
        for field in ("name", "owner", "models", "providers", "metadata"):
            if field in kwargs:
                body[field] = (
                    _stringify_metadata(kwargs[field])
                    if field == "metadata"
                    else kwargs[field]
                )
        body = UpdateKeyRequest.model_validate(body).model_dump(exclude_none=True)
        return self._request("PUT", f"/-/keys/{key_id}", json_body=body)

    # --- Config Management ---

    def update_config(self, config_patch):
        """Send a config patch to the gateway and trigger reload."""
        return self._request("PUT", "/-/config", json_body=config_patch)

    # --- Org Configs (multi-tenant) ---

    def set_org_config(self, org_id, config):
        """Push a per-org config to the gateway."""
        body = GatewayOrgConfig.model_validate(config).model_dump(
            by_alias=True,
            exclude_none=True,
        )
        return self._request("PUT", f"/-/orgs/{org_id}/config", json_body=body)

    def delete_org_config(self, org_id):
        """Remove a per-org config from the gateway."""
        return self._request("DELETE", f"/-/orgs/{org_id}/config")

    def get_org_config(self, org_id):
        """Fetch a per-org config from the gateway."""
        return self._request("GET", f"/-/orgs/{org_id}/config")

    def get_all_org_configs(self):
        """Fetch all per-org configs from the gateway."""
        return self._request("GET", "/-/orgs/configs")

    # --- Chat Completions (for playground testing) ---

    def send_chat_completion(
        self, prompt, model, api_key, system_prompt=None, cache_control=None
    ):
        """Send a real chat completion request through the gateway.

        Returns (status_code, body_dict, headers_dict) — does NOT raise on
        guardrail blocks (446) or warnings (246) so the caller can inspect them.
        """
        url = f"{self.base_url}/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        body = {"model": model, "messages": messages}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if cache_control:
            headers["Cache-Control"] = cache_control
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, json=body, headers=headers)
            resp_body = resp.json() if resp.content else {}
            resp_headers = dict(resp.headers)
            return resp.status_code, resp_body, resp_headers
        except httpx.ConnectError as e:
            raise GatewayClientError(f"Cannot connect to gateway at {url}: {e}") from e
        except httpx.TimeoutException as e:
            raise GatewayClientError(f"Gateway request timed out: {e}") from e

    # --- Batch API ---

    def submit_batch(self, requests, max_concurrency=5):
        return self._request(
            "POST",
            "/-/batches",
            json_body={"requests": requests, "max_concurrency": max_concurrency},
        )

    def get_batch(self, batch_id):
        return self._request(
            "GET", f"/-/batches/{batch_id}", params={"batch_id": batch_id}
        )

    def cancel_batch(self, batch_id):
        return self._request(
            "POST", f"/-/batches/{batch_id}/cancel", params={"batch_id": batch_id}
        )

    # --- MCP ---

    def mcp_status(self):
        """Get MCP gateway status: enabled, sessions, tools, servers."""
        return self._request("GET", "/-/mcp/status")

    def mcp_tools(self):
        """Get all registered MCP tools with stats."""
        return self._request("GET", "/-/mcp/tools")

    def mcp_test_tool(self, name, arguments=None):
        """Execute an MCP tool call in the playground."""
        return self._request(
            "POST",
            "/-/mcp/test",
            json_body={"name": name, "arguments": arguments or {}},
        )

    def mcp_resources(self):
        """Get all registered MCP resources."""
        return self._request("GET", "/-/mcp/resources")

    def mcp_prompts(self):
        """Get all registered MCP prompts."""
        return self._request("GET", "/-/mcp/prompts")

    # --- Metrics ---

    def get_metrics(self):
        return self._request("GET", "/-/metrics")


def get_gateway_client():
    """Factory: create a GatewayClient from environment variables."""
    return GatewayClient(
        base_url=AGENTCC_GATEWAY_INTERNAL_URL,
        admin_token=AGENTCC_ADMIN_TOKEN,
    )


def _stringify_metadata(metadata):
    """Gateway key metadata is a map[string]string; keep local JSON flexible."""
    if not isinstance(metadata, dict):
        return {}

    normalized = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized[str(key)] = value
        elif isinstance(value, bool):
            normalized[str(key)] = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            normalized[str(key)] = json.dumps(
                value, sort_keys=True, separators=(",", ":")
            )
        else:
            normalized[str(key)] = str(value)
    return normalized

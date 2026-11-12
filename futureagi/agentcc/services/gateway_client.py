import asyncio
import json
import os
import ssl
from contextlib import contextmanager
from time import monotonic

import httpx
import structlog

from agentcc.contracts.gateway_admin import (
    CreateKeyRequest,
    ImportKeysRequest,
    UpdateKeyRequest,
)
from agentcc.services.model_fallback_bounds import dump_stored_org_config
from tfc.ee_loader import is_cloud_env

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 10.0

# The sampling parameters a playground test may set; anything else is left to
# the model's defaults.
PLAYGROUND_PARAMS = ("temperature", "max_tokens", "top_p")

# A streamed answer can pause between chunks while the model thinks; the
# console gives the whole request 90 s.
STREAM_TIMEOUT = httpx.Timeout(30.0, read=90.0)


def resolve_gateway_public_url():
    return os.environ.get("AGENTCC_GATEWAY_URL", "http://localhost:8090")


def resolve_gateway_internal_url():
    # `AGENTCC_INTERNAL_URL` is the older docker-compose/.env key still used by
    # local stacks. Keep it as a compatibility fallback so container-to-container
    # gateway calls do not silently fall back to localhost inside the backend.
    # An explicit `AGENTCC_GATEWAY_URL` still wins for host-side tools; the
    # default is the in-network port (8090 is only the host-published port).
    return (
        os.environ.get("AGENTCC_GATEWAY_INTERNAL_URL")
        or os.environ.get("AGENTCC_INTERNAL_URL")
        or os.environ.get("AGENTCC_GATEWAY_URL")
        or "http://agentcc-gateway:8080"
    )


# Default gateway address — set via env vars in docker-compose / .env
AGENTCC_GATEWAY_URL = resolve_gateway_public_url()
# Internal URL for container-to-container communication (e.g. http://agentcc-gateway:8080)
AGENTCC_GATEWAY_INTERNAL_URL = resolve_gateway_internal_url()
AGENTCC_ADMIN_TOKEN = os.environ.get("AGENTCC_ADMIN_TOKEN", "")


def _note_missing_admin_token(token):
    """Runs on import, so it fires in every process that loads the module,
    one-off commands included. The compose files and the Helm chart always
    set the token; only on Future AGI Cloud (CLOUD_DEPLOYMENT) is its absence
    worth a warning."""
    if token:
        return
    log = logger.warning if is_cloud_env() else logger.debug
    log("AGENTCC_ADMIN_TOKEN not set — gateway admin API calls will be unauthenticated")


_note_missing_admin_token(AGENTCC_ADMIN_TOKEN)


# The header naming the API key a /-/batches submit's items run as.
BATCH_KEY_HEADER = "x-agentcc-batch-key"

# Headers only the client sets: a caller's extra headers never replace them.
_CLIENT_OWNED_HEADERS = frozenset({"authorization", "content-type"})


# Only documented managed-read codes and metadata may reach the Console.
# Keep the internal error_code discriminator separate for existing MCP callers.
_MANAGED_ERROR_MESSAGES = {
    "managed_models_disabled": "Managed models are not available on this deployment.",
    "managed_billing_unavailable": "Managed billing is temporarily unavailable. Please retry.",
    "gateway_unavailable": "The model service is temporarily unavailable. Please retry.",
    "managed_activation_required": (
        "Managed models are temporarily unavailable for this organization. "
        "Please retry later or contact your administrator."
    ),
    # get_decision_models: the gateway is older than the decision-model listing.
    "decisions_not_supported": (
        "This gateway version doesn't list decision models. "
        "Update the gateway to use them."
    ),
}


class GatewayClientError(Exception):
    def __init__(
        self, message, status_code=None, error_code=None, *, payload=None, headers=None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        error = payload.get("error") if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        code = error.get("code", error_code)
        self.code = (
            code
            if isinstance(code, str) and code in _MANAGED_ERROR_MESSAGES
            else "gateway_unavailable"
        )
        self.public_message = _MANAGED_ERROR_MESSAGES[self.code]
        self.metadata = {}
        metadata = error.get("metadata")
        if self.code == "managed_billing_unavailable" and isinstance(metadata, dict):
            if metadata.get("reason") in ("redis_unavailable", "account_state_missing"):
                self.metadata["reason"] = metadata["reason"]
            if metadata.get("remedy_hint") == "retry":
                self.metadata["remedy_hint"] = "retry"
        self.headers = {}
        retry_after = (headers or {}).get("Retry-After")
        # The gateway emits delta-seconds, never dates or free-form text.
        if (
            isinstance(retry_after, str)
            and 1 <= len(retry_after) <= 10
            and retry_after.isascii()
            and retry_after.isdecimal()
        ):
            self.headers["Retry-After"] = retry_after


def _tls_configuration_error(error):
    # HTTPX wraps httpcore's transport error, which wraps the typed SSL failure.
    # Inspect types rather than private/localized transport message strings.
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, ssl.SSLError) and not isinstance(
            error, (ssl.SSLEOFError, ssl.SSLZeroReturnError, ssl.SSLSyscallError)
        ):
            return error
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return None


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

    def _request(
        self,
        method,
        path,
        params=None,
        json_body=None,
        headers=None,
        *,
        timeout=DEFAULT_TIMEOUT,
    ):
        """Call the gateway admin API. ``headers`` are sent next to the admin
        token and the JSON content type, never in place of them: an
        Authorization or Content-Type among them (any case) is dropped."""
        url = f"{self.base_url}{path}"
        request_headers = self._headers()
        for name, value in (headers or {}).items():
            if name.lower() not in _CLIENT_OWNED_HEADERS:
                request_headers[name] = value
        try:
            try:
                http_client = httpx.Client(timeout=timeout)
            except ValueError:
                # HTTPX rejects malformed proxy schemes during client setup.
                # Scope this to construction, not response/body decoding errors.
                raise ValueError("Invalid gateway client configuration") from None
            with http_client as client:
                resp = client.request(
                    method,
                    url,
                    headers=request_headers,
                    params=params,
                    json=json_body,
                )
            if resp.status_code >= 400:
                # Preserve the machine-readable discriminator: MCP's missing
                # route and missing org configuration both use HTTP 404.
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                error = body.get("error") if isinstance(body, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                raise GatewayClientError(
                    f"Gateway returned {resp.status_code}.",
                    status_code=resp.status_code,
                    error_code=code if isinstance(code, str) else None,
                    payload=body,
                    headers=resp.headers,
                )
            return resp.json() if resp.content else {}
        except httpx.TimeoutException as e:
            raise GatewayClientError(f"Gateway request timed out: {e}") from e
        except (
            httpx.UnsupportedProtocol,
            httpx.LocalProtocolError,
            httpx.InvalidURL,
            httpx.ProxyError,
        ) as e:
            # HTTPX maps rejected CONNECT/SOCKS handshakes to ProxyError, not
            # HTTPStatusError. Fail closed regardless of untrusted reason text.
            # Actual proxy socket loss remains a Network/RemoteProtocolError.
            kind = "proxy" if isinstance(e, httpx.ProxyError) else "request"
            raise type(e)(f"Invalid gateway {kind} configuration") from None
        except httpx.TransportError as e:
            tls_error = _tls_configuration_error(e)
            if tls_error is not None:
                # TLS negotiation/verification failures are not offline gateways.
                # Suppress the chain, which can contain operator values.
                if isinstance(tls_error, ssl.SSLCertVerificationError):
                    raise ssl.SSLCertVerificationError(
                        "Gateway TLS certificate verification failed"
                    ) from None
                raise ssl.SSLError("Invalid gateway TLS configuration") from None
            # HTTPX 0.28.1: NetworkError covers connect/read/write/close loss;
            # RemoteProtocolError covers peer closes and malformed responses.
            # Base/unknown transport or protocol classes do not prove outage.
            if isinstance(e, (httpx.NetworkError, httpx.RemoteProtocolError)):
                raise GatewayClientError("Cannot connect to gateway") from e
            raise httpx.TransportError("Gateway transport failure") from None

    # --- Health ---

    def health_check(self):
        return self._request("GET", "/healthz")

    def ready_check(self):
        return self._request("GET", "/readyz")

    def admission_cap_ledger_status(self):
        """Bound the entire ledger probe, not each idle gap of a trickling body."""
        deadline = monotonic() + 2.0
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # This is a synchronous Django client, not an async view API.
            return "unknown"

        async def probe():
            remaining = deadline - monotonic()
            if remaining <= 0:
                return "unknown"
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(remaining), follow_redirects=False
                ) as client:
                    async with client.stream(
                        "GET", f"{self.base_url}/readyz", headers=self._headers()
                    ) as response:
                        if response.status_code >= 400:
                            return "unknown"
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > 64 * 1024:
                                return "unknown"
                            body.extend(chunk)
                        return parse_admission_cap_ledger(json.loads(body))

        loop = asyncio.new_event_loop()
        try:
            status = loop.run_until_complete(probe())
            # Include synchronous setup/decoding in the readiness decision too.
            return status if monotonic() < deadline else "unknown"
        except Exception:
            # Includes the total deadline, transport errors and invalid JSON.
            return "unknown"
        finally:
            # The async contexts close the response/client, also on cancellation.
            # Unlike asyncio.run(), do not wait here for a canceled OS DNS lookup
            # in the loop's executor: that would defeat the request deadline.
            loop.close()

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

    def _committed_key_command(self, method, path, body=None):
        """Narrow strict transport for committed personal-key effects only."""
        try:
            with httpx.Client(
                timeout=DEFAULT_TIMEOUT, follow_redirects=False
            ) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                    json=body,
                    follow_redirects=False,
                )
            # Both endpoints below specify 200. Other 2xx are not their contract.
            if response.status_code != 200 or not response.content:
                return None
            result = response.json()
            return result if isinstance(result, dict) else None
        except Exception:
            # No transport/server exception text, response or credential logging.
            return None

    def import_committed_key(self, key):
        """Use the additive hash import, never remote-generated identity."""
        body = ImportKeysRequest(keys=[key]).model_dump(exclude_none=True)
        result = self._committed_key_command("POST", "/-/keys/sync", body)
        return (
            isinstance(result, dict)
            and type(result.get("received")) is int
            and result["received"] == 1
            and type(result.get("loaded")) is int
            and result["loaded"] == 1
        )

    def revoke_key_confirmed(self, key_id):
        """Push revocation without treating a replica reply as authoritative.

        The current gateway's {status: revoked} acknowledges only its own
        in-memory store. Its void Redis publisher cannot acknowledge peers.
        There is NO authoritative revocation/readback contract at this head;
        even a valid 200 therefore stays pending. Do not trust an unknown
        response field purporting to supply that missing contract.
        """
        from urllib.parse import quote

        result = self._committed_key_command(
            "DELETE", f"/-/keys/{quote(str(key_id), safe='')}"
        )
        if result != {"status": "revoked"}:
            return False
        return False

    def import_keys(self, keys):
        """Load keys the gateway lacks, by hash; see gateway_key_payload."""
        body = ImportKeysRequest(keys=keys).model_dump(exclude_none=True)
        return self._request("POST", "/-/keys/sync", json_body=body)

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
        # Saved legacy chains must reach the gateway's truncation-with-warning
        # decoder too; the config writer bounds all new/changed chains.
        body = dump_stored_org_config(config)
        result = self._request("PUT", f"/-/orgs/{org_id}/config", json_body=body)
        ledger_status = parse_admission_cap_ledger(result)
        if ledger_status != "unknown":
            from agentcc.services.cap_ledger_status import remember_ledger_status

            remember_ledger_status(org_id, self.base_url, ledger_status)
        return result

    def delete_org_config(self, org_id):
        """Remove a per-org config from the gateway."""
        return self._request("DELETE", f"/-/orgs/{org_id}/config")

    def get_decision_models(self, org_id):
        """The org's BYOK decision catalog, using the gateway's public projection.

        An internal key cannot list /v1/decisions/models: that route needs a
        customer key. The admin sibling reads the same catalog for this org
        without creating a key or falling back to node-wide providers.
        """
        try:
            listing = self._request("GET", f"/-/orgs/{org_id}/decisions/models")
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise GatewayClientError("Invalid gateway response.", 502) from error
        except GatewayClientError as error:
            # A gateway older than this route (v1.58.0, or a roll that updates
            # the backend first) has no such route: its router answers 404
            # not_found. The route's handler never answers 404 itself (an org
            # with no config lists nothing), so the code names the old gateway.
            # Retrying cannot help, unlike a 5xx or an unreachable gateway.
            if error.status_code == 404 and error.error_code == "not_found":
                raise GatewayClientError(
                    "Gateway has no decision-model listing.",
                    501,
                    "decisions_not_supported",
                ) from error
            raise
        if (
            not isinstance(listing, dict)
            or listing.get("object") != "list"
            or not isinstance(listing.get("data"), list)
        ):
            raise GatewayClientError("Invalid gateway response.", 502)
        return listing

    # --- Managed models ---

    def get_catalog(self):
        """The managed providers' model catalog, with upstream and billed prices."""
        try:
            return self._request("GET", "/-/catalog")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Only managed reads normalize malformed response JSON. Keep the
            # shared transport's setup/proxy/TLS and health-read semantics.
            raise GatewayClientError("Invalid gateway response.", 502) from e

    def get_org_credits(self, org_id):
        """An org's managed-model credit balance."""
        try:
            return self._request("GET", f"/-/orgs/{org_id}/credits")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise GatewayClientError("Invalid gateway response.", 502) from e

    def add_org_credits(self, org_id, amount_usd):
        """Add (or, when negative, remove) managed-model credit for an org."""
        return self._request(
            "POST", f"/-/orgs/{org_id}/credits", json_body={"amount_usd": amount_usd}
        )

    def set_org_credits(self, org_id, balance_usd):
        """Set an org's managed-model credit balance outright."""
        return self._request(
            "PUT", f"/-/orgs/{org_id}/credits", json_body={"balance_usd": balance_usd}
        )

    def get_org_config(self, org_id):
        """Fetch a per-org config from the gateway."""
        return self._request("GET", f"/-/orgs/{org_id}/config")

    def get_all_org_configs(self):
        """Fetch all per-org configs from the gateway."""
        return self._request("GET", "/-/orgs/configs")

    # --- Chat Completions (for playground testing) ---

    @staticmethod
    def chat_body(prompt, model, system_prompt=None, params=None, stream=False):
        """The chat completion a playground test sends: the prompt (after an
        optional system prompt) and only the known sampling parameters given.
        A stream asks for the final usage chunk, which carries the cost."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        body = {"model": model, "messages": messages}
        for name in PLAYGROUND_PARAMS:
            value = (params or {}).get(name)
            if value is not None:
                body[name] = value
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    def _chat_headers(self, api_key, cache_control=None):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if cache_control:
            headers["Cache-Control"] = cache_control
        return headers

    def send_chat_completion(
        self,
        prompt,
        model,
        api_key,
        system_prompt=None,
        cache_control=None,
        params=None,
    ):
        """Send a real chat completion request through the gateway.

        Returns (status_code, body_dict, headers_dict) — does NOT raise on
        guardrail blocks (446) or warnings (246) so the caller can inspect them.
        """
        url = f"{self.base_url}/v1/chat/completions"
        body = self.chat_body(prompt, model, system_prompt, params)
        headers = self._chat_headers(api_key, cache_control)
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

    @contextmanager
    def stream_chat_completion(
        self,
        prompt,
        model,
        api_key,
        system_prompt=None,
        cache_control=None,
        params=None,
    ):
        """The same request, streamed: yields the open httpx response (status,
        headers, ``iter_lines()``). A refusal (446, 402, ...) arrives as a JSON
        body, not a stream. Connection failures and timeouts, at the start or
        mid-stream, raise GatewayClientError."""
        url = f"{self.base_url}/v1/chat/completions"
        body = self.chat_body(prompt, model, system_prompt, params, stream=True)
        headers = self._chat_headers(api_key, cache_control)
        try:
            with httpx.Client(timeout=STREAM_TIMEOUT) as client:
                with client.stream("POST", url, json=body, headers=headers) as resp:
                    yield resp
        except httpx.ConnectError as e:
            raise GatewayClientError(f"Cannot connect to gateway at {url}: {e}") from e
        except httpx.TimeoutException as e:
            raise GatewayClientError(f"Gateway request timed out: {e}") from e
        except httpx.HTTPError as e:
            raise GatewayClientError(f"Gateway stream failed: {e}") from e

    def send_delegated_chat_completion(self, delegation, **kwargs):
        from agentcc.services.delegated_calls import chat

        return chat(self, delegation, **kwargs)

    def stream_delegated_chat_completion(self, delegation, **kwargs):
        from agentcc.services.delegated_calls import stream_chat

        return stream_chat(self, delegation, **kwargs)

    def list_delegated_batches(self, delegation=None, **query):
        """A signed list has no query; background reconciliation uses admin mode."""
        if delegation is None:
            return self._request("GET", "/-/batches")
        return self._delegated_batch_request(delegation, "GET", "/-/batches")

    def get_delegated_batch(self, delegation, batch_id, /, **query):
        return self._delegated_batch_request(
            delegation, "GET", f"/-/batches/{batch_id}"
        )

    def cancel_delegated_batch(self, delegation, batch_id, /, **query):
        return self._delegated_batch_request(
            delegation, "POST", f"/-/batches/{batch_id}/cancel"
        )

    def _delegated_batch_request(self, delegation, method, path):
        from agentcc.services.delegated_calls import _headers

        # No caller parameter is forwarded, including org_id. The exact method
        # and query-free route are validated and signed before any network I/O.
        headers = _headers(self, delegation, path, b"", method=method)
        return self._request(method, path, headers=headers)

    def submit_delegated_batch(self, delegation, requests, max_concurrency=5):
        from agentcc.services.delegated_calls import submit_batch

        return submit_batch(self, delegation, requests, max_concurrency)

    # --- Batch API ---

    def submit_batch(self, requests, max_concurrency=5, batch_key=None):
        """Submit a batch whose items run as ``batch_key``: the gateway runs
        each item as a chat request authenticated by that key (its org's
        providers, guardrails, budgets, credits and request log), and with
        auth enabled refuses a submit without one (400 batch_key_required)."""
        return self._request(
            "POST",
            "/-/batches",
            json_body={"requests": requests, "max_concurrency": max_concurrency},
            headers={BATCH_KEY_HEADER: batch_key} if batch_key else None,
        )

    def get_batch(self, batch_id, org_id=None):
        """The gateway answers 404 unless ``org_id`` is the batch's org."""
        params = {"batch_id": batch_id}
        if org_id is not None:
            params["org_id"] = str(org_id)
        return self._request("GET", f"/-/batches/{batch_id}", params=params)

    def cancel_batch(self, batch_id, org_id=None):
        """The gateway answers 404 unless ``org_id`` is the batch's org."""
        params = {"batch_id": batch_id}
        if org_id is not None:
            params["org_id"] = str(org_id)
        return self._request("POST", f"/-/batches/{batch_id}/cancel", params=params)

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


def parse_admission_cap_ledger(payload):
    """Only the gateway's documented enum is evidence of ledger availability."""
    value = payload.get("admission_cap_ledger") if isinstance(payload, dict) else None
    return value if value in ("ready", "absent", "unavailable") else "unknown"


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

import asyncio
import json
import time
from urllib.parse import urlparse

import httpx
import structlog
from django.conf import settings
from django.utils import timezone

from ee.falcon_ai.credentials import (
    AUTH_REQUIRED,
    FalconCredentialError,
    decrypt_falcon_token,
)
from ee.falcon_ai.oauth_registration import save_connector
from ee.falcon_ai.url_fetcher import _names_a_public_host
from tfc.ee_loader import is_cloud_env
from tfc.utils.ssrf_guard import (
    EndpointPolicy,
    guarded_async_httpx_client,
    guarded_httpx_client,
    model_endpoint_policy,
)

logger = structlog.get_logger(__name__)


class ConnectorAddressRefused(ValueError):
    """A connector address this deployment does not call."""


def on_future_agi_cloud():
    """Whether this deployment is Future AGI Cloud, read the way the address
    check reads it."""
    deployment = (
        getattr(settings, "CLOUD_DEPLOYMENT", None) if settings.configured else None
    )
    return is_cloud_env(deployment)


def check_connector_call_address(url):
    """Refuse, before any lookup, an address this deployment does not call.

    On Future AGI Cloud a connector is called over https:// only. Where only
    public addresses are allowed, a host that no public server has is not
    looked up. The addresses the host resolves to are checked by the address
    check's client when it connects.
    """
    if on_future_agi_cloud() and urlparse(url).scheme.lower() != "https":
        raise ConnectorAddressRefused("https:// is required on Future AGI Cloud")
    if (
        model_endpoint_policy() is EndpointPolicy.PUBLIC_ONLY
        and not _names_a_public_host(url)
    ):
        raise ConnectorAddressRefused("the host is not a public name")


NOT_A_CONNECTOR = (
    "This server answered, but not as a connector. Check the server address."
)


class NotAConnectorAnswer(ValueError):
    """A server answered, but not with an answer of the connector protocol."""


def _connector_answer(data, request_id):
    """``data`` when it is a JSON-RPC 2.0 answer to the request ``request_id``
    that carries a result or an error; otherwise NotAConnectorAnswer."""
    if (
        isinstance(data, dict)
        and data.get("jsonrpc") == "2.0"
        and data.get("id") == request_id
        and ("result" in data or "error" in data)
    ):
        return data
    raise NotAConnectorAnswer("not an answer of the connector protocol")


def _carries(data, name):
    """Whether a connector's answer has a result that carries a ``name`` list."""
    result = data.get("result")
    return isinstance(result, dict) and isinstance(result.get(name), list)


def _result_carrying(data, name):
    """The result of a connector's answer, which carries a ``name`` list.

    A connector's error answer is a failed request; a result without the list
    is not an answer the call can use.
    """
    if "result" not in data:
        raise RuntimeError("the connector answered with an error")
    if not _carries(data, name):
        raise NotAConnectorAnswer("the result does not carry what the call needs")
    return data["result"]


class MCPConnectorProxy:
    """Proxy for calling tools on external MCP servers.

    Sessions are cached per connector and URL to avoid reinitializing on every
    call.
    """

    TIMEOUT = 30.0  # seconds per call
    _request_id = 0
    # Cache: (connector id, connector_url) -> (session_id, endpoint_url)
    #
    # Session cache is process-level (not shared across workers).
    # In multi-worker deployments (e.g., gunicorn with multiple workers),
    # each worker maintains its own cache. This is intentional -- sessions
    # are lightweight and duplicate init is harmless.
    _session_cache: dict = {}

    def _check_background(self):
        guard = getattr(self, "background_analysis", None)
        if guard is not None:
            guard.check()

    async def _check_background_async(self):
        guard = getattr(self, "background_analysis", None)
        if guard is not None:
            await guard.checkpoint()

    def _background_timeout(self, timeout):
        guard = getattr(self, "background_analysis", None)
        return min(timeout, max(0.001, guard.remaining)) if guard else timeout

    def _next_id(self):
        MCPConnectorProxy._request_id += 1
        return MCPConnectorProxy._request_id

    def _get_endpoint_urls(self, connector):
        """Return a list of URLs to try for JSON-RPC requests.

        For streamable HTTP: try the URL as-is, then with /mcp suffix.
        For SSE: try replacing /sse with /mcp (streamable HTTP), then the
        URL as-is (some SSE servers accept JSON-RPC POSTs at the same URL).

        This handles servers that expose /mcp for streamable HTTP
        but may be configured with /sse in the connector URL.
        """
        url = connector.server_url.rstrip("/")
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")

        urls = []

        if connector.transport == "sse":
            # SSE servers: try streamable HTTP at /mcp first, then original URL
            if path.endswith("/sse"):
                mcp_path = path[:-4] + "/mcp"
                urls.append(base + mcp_path)
            elif not path.endswith("/mcp"):
                urls.append(base + path + "/mcp")
            urls.append(url)
        else:
            # Streamable HTTP: try URL as-is, then append /mcp if not already
            urls.append(url)
            if not path.endswith("/mcp"):
                urls.append(base + path + "/mcp")

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    def _parse_response(self, response, request_id):
        """Parse an MCP response — handles both JSON and SSE-wrapped JSON-RPC.

        In an event stream the answer is the data line whose id is
        ``request_id``; a stream without that line is not a connector answer.
        """
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            # SSE response — extract JSON-RPC from event data
            for line in response.text.splitlines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict) and data.get("id") == request_id:
                        return data
            raise NotAConnectorAnswer("no answer to the request in the event stream")
        return response.json()

    def _post_jsonrpc(self, url, payload, headers, timeout=None):
        """POST a JSON-RPC request and return the parsed response.

        Raises on HTTP errors, non-JSON responses and answers that are not a
        JSON-RPC 2.0 answer to this request.
        """
        timeout = timeout or self.TIMEOUT
        self._check_background()
        check_connector_call_address(url)
        with guarded_httpx_client(timeout=self._background_timeout(timeout)) as client:
            response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        request_id = payload.get("id")
        answer = self._parse_response(response, request_id)
        return _connector_answer(answer, request_id)

    async def _async_post_jsonrpc(self, url, payload, headers, timeout=None):
        """Async version of _post_jsonrpc."""
        timeout = timeout or self.TIMEOUT
        check_connector_call_address(url)
        async with guarded_async_httpx_client(
            timeout=self._background_timeout(timeout)
        ) as client:
            await self._check_background_async()
            response = await client.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._background_timeout(timeout),
            )
            response.raise_for_status()
            request_id = payload.get("id")
            answer = self._parse_response(response, request_id)
            return _connector_answer(answer, request_id)

    def _initialize_session(self, url, headers, timeout=None):
        """Perform MCP initialize handshake and return the Mcp-Session-Id.

        MCP streamable HTTP requires: initialize → initialized notification → ready.
        Returns session_id string or None if server doesn't use sessions.
        """
        timeout = timeout or self.TIMEOUT
        init_payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "FutureAGI-FalconAI", "version": "1.0.0"},
            },
        }
        self._check_background()
        check_connector_call_address(url)
        with guarded_httpx_client(timeout=self._background_timeout(timeout)) as client:
            response = client.post(url, json=init_payload, headers=headers)
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")

        # Send initialized notification (no id = notification)
        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        notif_headers = {**headers}
        if session_id:
            notif_headers["Mcp-Session-Id"] = session_id
        self._check_background()
        with guarded_httpx_client(timeout=self._background_timeout(timeout)) as client:
            client.post(url, json=notif_payload, headers=notif_headers)

        return session_id

    async def _async_initialize_session(self, url, headers, timeout=None):
        """Async version of _initialize_session."""
        timeout = timeout or self.TIMEOUT
        init_payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "FutureAGI-FalconAI", "version": "1.0.0"},
            },
        }
        check_connector_call_address(url)
        async with guarded_async_httpx_client(
            timeout=self._background_timeout(timeout)
        ) as client:
            await self._check_background_async()
            response = await client.post(
                url,
                json=init_payload,
                headers=headers,
                timeout=self._background_timeout(timeout),
            )
            response.raise_for_status()
            session_id = response.headers.get("mcp-session-id")

            notif_payload = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            notif_headers = {**headers}
            if session_id:
                notif_headers["Mcp-Session-Id"] = session_id
            await self._check_background_async()
            await client.post(
                url,
                json=notif_payload,
                headers=notif_headers,
                timeout=self._background_timeout(timeout),
            )

            return session_id

    def _session_key(self, connector):
        """A session belongs to one connector at one address."""
        return (connector.pk, connector.server_url)

    def _get_cached_session(self, connector):
        """Get cached session for a connector, if any."""
        cache_key = self._session_key(connector)
        cached = self._session_cache.get(cache_key)
        if cached:
            return cached  # (session_id, endpoint_url)
        return None, None

    def _cache_session(self, connector, session_id, url):
        """Cache a successful session for reuse."""
        if session_id:
            self._session_cache[self._session_key(connector)] = (session_id, url)

    def _clear_session(self, connector):
        """Clear cached session (e.g. on expiry)."""
        self._session_cache.pop(self._session_key(connector), None)

    def _post_with_fallback(
        self, connector, payload, headers, timeout=None, needs=None
    ):
        """Try posting to each candidate URL, returning first success.

        Uses cached sessions when available to skip re-initialization.
        Returns (url, data) tuple on success. After a fresh request the success
        line is logged only for an answer whose result carries the ``needs``
        list. When no candidate succeeds, raises the failure of a candidate
        that answered outside the connector protocol if one did, and the last
        exception otherwise.
        """
        # Try cached session first
        cached_sid, cached_url = self._get_cached_session(connector)
        if cached_sid and cached_url:
            try:
                sess_headers = {**headers, "Mcp-Session-Id": cached_sid}
                data = self._post_jsonrpc(cached_url, payload, sess_headers, timeout)
                return cached_url, data
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 401, 403):
                    # Session expired — clear and fall through to fresh init
                    self._clear_session(connector)
                else:
                    raise

        # No cache or cache miss — try URLs with initialization
        urls = self._get_endpoint_urls(connector)
        last_error = None
        outside_the_protocol = None
        for url in urls:
            try:
                data = self._post_jsonrpc(url, payload, headers, timeout)
                if _carries(data, needs):
                    logger.debug(
                        "mcp_endpoint_success", url=url, connector=connector.name
                    )
                return url, data
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 405):
                    try:
                        session_id = self._initialize_session(url, headers, timeout)
                        sess_headers = {**headers}
                        if session_id:
                            sess_headers["Mcp-Session-Id"] = session_id
                        data = self._post_jsonrpc(url, payload, sess_headers, timeout)
                        if _carries(data, needs):
                            logger.debug(
                                "mcp_endpoint_success",
                                url=url,
                                connector=connector.name,
                            )
                        # Cache this session for future calls
                        self._cache_session(connector, session_id, url)
                        return url, data
                    except Exception as init_err:
                        logger.debug(
                            "mcp_init_then_request_failed",
                            error_class=type(init_err).__name__,
                            error="MCP initialization failed",
                        )
                        last_error = init_err
                else:
                    logger.debug(
                        "mcp_endpoint_attempt_failed",
                        error_class=type(e).__name__,
                        error="MCP request failed",
                    )
                    last_error = e
            except Exception as e:
                logger.debug(
                    "mcp_endpoint_attempt_failed",
                    error_class=type(e).__name__,
                    error="MCP request failed",
                )
                last_error = e
            if isinstance(last_error, NotAConnectorAnswer):
                outside_the_protocol = last_error
        # A server that answered outside the connector protocol at one of the
        # addresses is told as such when no address answered as a connector.
        raise outside_the_protocol or last_error

    async def _async_post_with_fallback(
        self, connector, payload, headers, timeout=None, needs=None
    ):
        """Async version of _post_with_fallback with session caching."""
        # Try cached session first
        cached_sid, cached_url = self._get_cached_session(connector)
        if cached_sid and cached_url:
            try:
                sess_headers = {**headers, "Mcp-Session-Id": cached_sid}
                data = await self._async_post_jsonrpc(
                    cached_url, payload, sess_headers, timeout
                )
                return cached_url, data
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 401, 403):
                    self._clear_session(connector)
                else:
                    raise

        urls = self._get_endpoint_urls(connector)
        last_error = None
        outside_the_protocol = None
        for url in urls:
            try:
                data = await self._async_post_jsonrpc(url, payload, headers, timeout)
                if _carries(data, needs):
                    logger.debug(
                        "mcp_endpoint_success", url=url, connector=connector.name
                    )
                return url, data
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 405):
                    try:
                        session_id = await self._async_initialize_session(
                            url, headers, timeout
                        )
                        sess_headers = {**headers}
                        if session_id:
                            sess_headers["Mcp-Session-Id"] = session_id
                        data = await self._async_post_jsonrpc(
                            url, payload, sess_headers, timeout
                        )
                        if _carries(data, needs):
                            logger.debug(
                                "mcp_endpoint_success",
                                url=url,
                                connector=connector.name,
                            )
                        self._cache_session(connector, session_id, url)
                        return url, data
                    except Exception as init_err:
                        logger.debug(
                            "mcp_init_then_request_failed",
                            error_class=type(init_err).__name__,
                            error="MCP initialization failed",
                        )
                        last_error = init_err
                else:
                    logger.debug(
                        "mcp_endpoint_attempt_failed",
                        error_class=type(e).__name__,
                        error="MCP request failed",
                    )
                    last_error = e
            except Exception as e:
                logger.debug(
                    "mcp_endpoint_attempt_failed",
                    error_class=type(e).__name__,
                    error="MCP request failed",
                )
                last_error = e
            if isinstance(last_error, NotAConnectorAnswer):
                outside_the_protocol = last_error
        # A server that answered outside the connector protocol at one of the
        # addresses is told as such when no address answered as a connector.
        raise outside_the_protocol or last_error

    async def discover_tools(self, connector):
        """Discover available tools from an external MCP server.

        Sends a JSON-RPC tools/list request and caches the result.
        Tries streamable HTTP first, falls back to SSE endpoint.
        """
        try:
            headers = self._build_headers(connector)
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }

            url, data = await self._async_post_with_fallback(
                connector, payload, headers, needs="tools"
            )

            tools = _result_carrying(data, "tools")["tools"]

            # Update connector with discovered tools
            from channels.db import database_sync_to_async

            await database_sync_to_async(self._save_discovery)(connector, tools)

            return {"success": True, "tools": tools, "count": len(tools)}
        except FalconCredentialError:
            from channels.db import database_sync_to_async

            await database_sync_to_async(self._save_error)(connector, AUTH_REQUIRED)
            return {"success": False, "error": AUTH_REQUIRED, "code": AUTH_REQUIRED}
        except NotAConnectorAnswer as error:
            logger.error("mcp_discovery_error", error_class=type(error).__name__)
            from channels.db import database_sync_to_async

            await database_sync_to_async(self._save_error)(connector, NOT_A_CONNECTOR)
            return {"success": False, "error": NOT_A_CONNECTOR}
        except Exception:
            logger.error(
                "mcp_discovery_error",
                connector=connector.name,
                error="MCP request failed",
            )
            from channels.db import database_sync_to_async

            await database_sync_to_async(self._save_error)(
                connector, "MCP request failed"
            )
            return {"success": False, "error": "MCP request failed"}

    def discover_tools_sync(self, connector):
        """Synchronous version for REST API views."""
        try:
            headers = self._build_headers(connector)
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }

            url, data = self._post_with_fallback(
                connector, payload, headers, needs="tools"
            )

            tools = _result_carrying(data, "tools")["tools"]
            self._save_discovery(connector, tools)
            return {"success": True, "tools": tools, "count": len(tools)}
        except FalconCredentialError:
            self._save_error(connector, AUTH_REQUIRED)
            return {"success": False, "error": AUTH_REQUIRED, "code": AUTH_REQUIRED}
        except NotAConnectorAnswer as error:
            logger.error("mcp_discovery_error", error_class=type(error).__name__)
            self._save_error(connector, NOT_A_CONNECTOR)
            return {"success": False, "error": NOT_A_CONNECTOR}
        except Exception:
            logger.error(
                "mcp_discovery_error",
                connector=connector.name,
                error="MCP request failed",
            )
            self._save_error(connector, "MCP request failed")
            return {"success": False, "error": "MCP request failed"}

    async def execute_tool(self, connector, tool_name, params):
        """Execute a tool on an external MCP server.

        Returns a ToolResult-like dict with content and is_error.
        Tries streamable HTTP first, falls back to SSE endpoint.
        """
        try:
            if getattr(self, "background_analysis", None) is not None:
                from channels.db import database_sync_to_async

                # OAuth refresh is synchronous. Keep it off the loop so the
                # background deadline can cancel the awaiting agent promptly.
                headers = await database_sync_to_async(self._build_headers)(connector)
            else:
                headers = self._build_headers(connector)
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": params or {},
                },
            }

            for attempt in range(3):
                try:
                    url, data = await self._async_post_with_fallback(
                        connector, payload, headers, needs="content"
                    )

                    result = _result_carrying(data, "content")
                    # MCP returns content as array of {type, text} blocks
                    content_blocks = result.get("content", [])
                    text_content = "\n".join(
                        block.get("text", "")
                        for block in content_blocks
                        if block.get("type") == "text"
                    )
                    is_error = result.get("isError", False)

                    return {
                        "content": text_content or json.dumps(result),
                        "is_error": is_error,
                    }
                except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                    if attempt == 2 or (
                        isinstance(e, httpx.HTTPStatusError)
                        and e.response.status_code < 500
                    ):
                        raise
                    logger.warning(
                        "mcp_tool_retry",
                        connector=connector.name,
                        tool=tool_name,
                        attempt=attempt + 1,
                        error="MCP request failed",
                    )
                    await self._check_background_async()
                    await asyncio.sleep(self._background_timeout(1))
        except Exception as error:
            # Access revocation takes precedence over a connector auth result.
            await self._check_background_async()
            if isinstance(error, FalconCredentialError):
                return {
                    "content": AUTH_REQUIRED,
                    "is_error": True,
                    "code": AUTH_REQUIRED,
                }
            logger.error(
                "mcp_tool_call_error",
                connector=connector.name,
                tool=tool_name,
                error="MCP request failed",
            )
            return {
                "content": "MCP tool call failed",
                "is_error": True,
            }

    def execute_tool_sync(self, connector, tool_name, params):
        """Synchronous version of execute_tool for use in threaded contexts."""
        try:
            headers = self._build_headers(connector)
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": params or {},
                },
            }

            for attempt in range(3):
                try:
                    url, data = self._post_with_fallback(
                        connector, payload, headers, needs="content"
                    )

                    result = _result_carrying(data, "content")
                    content_blocks = result.get("content", [])
                    text_content = "\n".join(
                        block.get("text", "")
                        for block in content_blocks
                        if block.get("type") == "text"
                    )
                    is_error = result.get("isError", False)

                    return {
                        "content": text_content or json.dumps(result),
                        "is_error": is_error,
                    }
                except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                    if attempt == 2 or (
                        isinstance(e, httpx.HTTPStatusError)
                        and e.response.status_code < 500
                    ):
                        raise
                    logger.warning(
                        "mcp_tool_retry",
                        connector=connector.name,
                        tool=tool_name,
                        attempt=attempt + 1,
                        error="MCP request failed",
                    )
                    self._check_background()
                    time.sleep(self._background_timeout(1))
        except Exception as error:
            self._check_background()
            if isinstance(error, FalconCredentialError):
                return {
                    "content": AUTH_REQUIRED,
                    "is_error": True,
                    "code": AUTH_REQUIRED,
                }
            logger.error(
                "mcp_tool_call_error",
                connector=connector.name,
                tool=tool_name,
                error="MCP request failed",
            )
            return {
                "content": "MCP tool call failed",
                "is_error": True,
            }

    def test_connection_sync(self, connector):
        """Test if we can connect to the MCP server. Synchronous for REST API."""
        try:
            headers = self._build_headers(connector)
            # Try a simple tools/list call with fallback
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
            url, data = self._post_with_fallback(
                connector, payload, headers, timeout=10.0, needs="tools"
            )
            _result_carrying(data, "tools")
            return {"success": True, "status_code": 200}
        except FalconCredentialError:
            return {"success": False, "error": AUTH_REQUIRED, "code": AUTH_REQUIRED}
        except NotAConnectorAnswer:
            return {"success": False, "error": NOT_A_CONNECTOR}
        except Exception:
            return {"success": False, "error": "MCP request failed"}

    def _build_headers(self, connector):
        """Build auth headers for the MCP server."""
        self._check_background()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if connector.auth_type in ("api_key", "bearer"):
            value = decrypt_falcon_token(connector.auth_header_value)
            if not value:
                raise FalconCredentialError("Connector credential is missing")
            if connector.auth_type == "api_key":
                headers[connector.auth_header_name] = value
            else:
                headers["Authorization"] = f"Bearer {value}"
        elif connector.auth_type == "oauth":
            from ee.falcon_ai.oauth_client import get_valid_access_token

            # Never downgrade a credential failure into an anonymous request.
            access_token = get_valid_access_token(connector)
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def _save_discovery(self, connector, tools):
        """Save discovered tools to the connector model."""
        save_connector(
            connector,
            {
                "discovered_tools": tools,
                "enabled_tool_names": [t.get("name", "") for t in tools],
                "is_verified": True,
                "last_discovery_at": timezone.now(),
                "last_error": "",
            },
        )

    def _save_error(self, connector, error):
        """Save error to the connector model."""
        save_connector(
            connector,
            {"last_error": error[:500], "is_verified": False},
            discard_stale=True,
        )

import asyncio
import json
import time
from urllib.parse import urlparse

import httpx
import structlog
from django.utils import timezone

from agentcc.services.credential_manager import decrypt_token

logger = structlog.get_logger(__name__)


class MCPConnectorProxy:
    """Proxy for calling tools on external MCP servers.

    Sessions are cached per connector URL to avoid reinitializing on every call.
    """

    TIMEOUT = 30.0  # seconds per call
    _request_id = 0
    # Cache: connector_url -> (session_id, endpoint_url)
    #
    # Session cache is process-level (not shared across workers).
    # In multi-worker deployments (e.g., gunicorn with multiple workers),
    # each worker maintains its own cache. This is intentional -- sessions
    # are lightweight and duplicate init is harmless.
    _session_cache: dict = {}

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

    def _parse_response(self, response):
        """Parse an MCP response — handles both JSON and SSE-wrapped JSON-RPC."""
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            # SSE response — extract JSON-RPC from event data
            for line in response.text.splitlines():
                if line.startswith("data: "):
                    try:
                        return json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
            raise ValueError("No valid JSON-RPC found in SSE response")
        return response.json()

    def _post_jsonrpc(self, url, payload, headers, timeout=None):
        """POST a JSON-RPC request and return the parsed response.

        Raises on HTTP errors or non-JSON responses.
        """
        timeout = timeout or self.TIMEOUT
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return self._parse_response(response)

    async def _async_post_jsonrpc(self, url, payload, headers, timeout=None):
        """Async version of _post_jsonrpc."""
        timeout = timeout or self.TIMEOUT
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return self._parse_response(response)

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
        response = httpx.post(url, json=init_payload, headers=headers, timeout=timeout)
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
        httpx.post(url, json=notif_payload, headers=notif_headers, timeout=timeout)

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
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=init_payload, headers=headers)
            response.raise_for_status()
            session_id = response.headers.get("mcp-session-id")

            notif_payload = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            notif_headers = {**headers}
            if session_id:
                notif_headers["Mcp-Session-Id"] = session_id
            await client.post(url, json=notif_payload, headers=notif_headers)

            return session_id

    def _get_cached_session(self, connector):
        """Get cached session for a connector, if any."""
        cache_key = connector.server_url
        cached = self._session_cache.get(cache_key)
        if cached:
            return cached  # (session_id, endpoint_url)
        return None, None

    def _cache_session(self, connector, session_id, url):
        """Cache a successful session for reuse."""
        if session_id:
            self._session_cache[connector.server_url] = (session_id, url)

    def _clear_session(self, connector):
        """Clear cached session (e.g. on expiry)."""
        self._session_cache.pop(connector.server_url, None)

    def _post_with_fallback(self, connector, payload, headers, timeout=None):
        """Try posting to each candidate URL, returning first success.

        Uses cached sessions when available to skip re-initialization.
        Returns (url, data) tuple on success, raises last exception on failure.
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
        for url in urls:
            try:
                data = self._post_jsonrpc(url, payload, headers, timeout)
                logger.debug("mcp_endpoint_success", url=url, connector=connector.name)
                return url, data
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 405):
                    try:
                        session_id = self._initialize_session(url, headers, timeout)
                        sess_headers = {**headers}
                        if session_id:
                            sess_headers["Mcp-Session-Id"] = session_id
                        data = self._post_jsonrpc(url, payload, sess_headers, timeout)
                        logger.debug(
                            "mcp_endpoint_success", url=url, connector=connector.name
                        )
                        # Cache this session for future calls
                        self._cache_session(connector, session_id, url)
                        return url, data
                    except Exception as init_err:
                        logger.debug(
                            "mcp_init_then_request_failed", url=url, error=str(init_err)
                        )
                        last_error = init_err
                else:
                    logger.debug("mcp_endpoint_attempt_failed", url=url, error=str(e))
                    last_error = e
            except Exception as e:
                logger.debug("mcp_endpoint_attempt_failed", url=url, error=str(e))
                last_error = e
        raise last_error

    async def _async_post_with_fallback(
        self, connector, payload, headers, timeout=None
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
        for url in urls:
            try:
                data = await self._async_post_jsonrpc(url, payload, headers, timeout)
                logger.debug("mcp_endpoint_success", url=url, connector=connector.name)
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
                        logger.debug(
                            "mcp_endpoint_success", url=url, connector=connector.name
                        )
                        self._cache_session(connector, session_id, url)
                        return url, data
                    except Exception as init_err:
                        logger.debug(
                            "mcp_init_then_request_failed", url=url, error=str(init_err)
                        )
                        last_error = init_err
                else:
                    logger.debug("mcp_endpoint_attempt_failed", url=url, error=str(e))
                    last_error = e
            except Exception as e:
                logger.debug("mcp_endpoint_attempt_failed", url=url, error=str(e))
                last_error = e
        raise last_error

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
                connector, payload, headers
            )

            tools = data.get("result", {}).get("tools", [])

            # Update connector with discovered tools
            from channels.db import database_sync_to_async

            await database_sync_to_async(self._save_discovery)(connector, tools)

            return {"success": True, "tools": tools, "count": len(tools)}
        except Exception as e:
            logger.error("mcp_discovery_error", connector=connector.name, error=str(e))
            from channels.db import database_sync_to_async

            await database_sync_to_async(self._save_error)(connector, str(e))
            return {"success": False, "error": str(e)}

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

            url, data = self._post_with_fallback(connector, payload, headers)

            tools = data.get("result", {}).get("tools", [])
            self._save_discovery(connector, tools)
            return {"success": True, "tools": tools, "count": len(tools)}
        except Exception as e:
            logger.error("mcp_discovery_error", connector=connector.name, error=str(e))
            self._save_error(connector, str(e))
            return {"success": False, "error": str(e)}

    async def execute_tool(self, connector, tool_name, params):
        """Execute a tool on an external MCP server.

        Returns a ToolResult-like dict with content and is_error.
        Tries streamable HTTP first, falls back to SSE endpoint.
        """
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
                    url, data = await self._async_post_with_fallback(
                        connector, payload, headers
                    )

                    result = data.get("result", {})
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
                        error=str(e),
                    )
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(
                "mcp_tool_call_error",
                connector=connector.name,
                tool=tool_name,
                error=str(e),
            )
            return {
                "content": f"Error calling {connector.name}.{tool_name}: {str(e)}",
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
                    url, data = self._post_with_fallback(connector, payload, headers)

                    result = data.get("result", {})
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
                        error=str(e),
                    )
                    time.sleep(1)
        except Exception as e:
            logger.error(
                "mcp_tool_call_error",
                connector=connector.name,
                tool=tool_name,
                error=str(e),
            )
            return {
                "content": f"Error calling {connector.name}.{tool_name}: {str(e)}",
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
                connector, payload, headers, timeout=10.0
            )
            return {"success": True, "status_code": 200}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _build_headers(self, connector):
        """Build auth headers for the MCP server."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if connector.auth_type == "api_key":
            headers[connector.auth_header_name] = decrypt_token(
                connector.auth_header_value
            )
        elif connector.auth_type == "bearer":
            headers["Authorization"] = (
                f"Bearer {decrypt_token(connector.auth_header_value)}"
            )
        elif connector.auth_type == "oauth":
            from ee.falcon_ai.oauth_client import get_valid_access_token

            try:
                access_token = get_valid_access_token(connector)
                headers["Authorization"] = f"Bearer {access_token}"
            except RuntimeError as e:
                logger.error(
                    "oauth_header_error",
                    connector=connector.name,
                    error=str(e),
                )
        return headers

    def _save_discovery(self, connector, tools):
        """Save discovered tools to the connector model."""
        connector.discovered_tools = tools
        connector.enabled_tool_names = [t.get("name", "") for t in tools]
        connector.is_verified = True
        connector.last_discovery_at = timezone.now()
        connector.last_error = ""
        connector.save(
            update_fields=[
                "discovered_tools",
                "enabled_tool_names",
                "is_verified",
                "last_discovery_at",
                "last_error",
                "updated_at",
            ]
        )

    def _save_error(self, connector, error):
        """Save error to the connector model."""
        connector.last_error = error[:500]
        connector.is_verified = False
        connector.save(update_fields=["last_error", "is_verified", "updated_at"])

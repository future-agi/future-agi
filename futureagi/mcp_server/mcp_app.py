"""Python MCP server backed by OpenAPI-generated Django API tools.

Authentication: API key or OAuth Bearer token on each request.

Streamable HTTP uses a single /mcp endpoint (stateless) — no persistent connections,
no session affinity, survives server restarts, horizontally scalable.
"""

import json
import os
import time

import structlog
from asgiref.sync import sync_to_async
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server.api_executor import APIExecutionError, MCPRequestContext, executor
from mcp_server.generated_registry import registry
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)

logger = structlog.get_logger(__name__)

# Build allowed hosts from MCP_SERVER_BASE_URL for DNS rebinding protection.
_mcp_base_url = os.environ.get("MCP_SERVER_BASE_URL", "")
_allowed_hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
if _mcp_base_url:
    from urllib.parse import urlparse

    _parsed = urlparse(_mcp_base_url)
    if _parsed.hostname:
        _allowed_hosts.append(_parsed.hostname)
        if _parsed.port:
            _allowed_hosts.append(f"{_parsed.hostname}:{_parsed.port}")

# Create the low-level MCP server so inputSchema comes directly from OpenAPI.
mcp = Server(
    "Future AGI",
    instructions=(
        "You are connected to the Future AGI platform. "
        "Use the available tools to explore evaluations, datasets, traces, "
        "and other resources in your workspace."
    ),
)

_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed_hosts,
)


def _authenticate_and_set_context(
    api_key: str, secret_key: str
) -> MCPRequestContext | None:
    """Authenticate via API key and set per-request context."""
    from accounts.models.user import OrgApiKey, User
    from accounts.models.workspace import Workspace

    try:
        # Use .all() to bypass BaseModelManager workspace filtering
        org_api_key = (
            OrgApiKey.objects.all()
            .select_related("organization", "workspace")
            .get(api_key=api_key, secret_key=secret_key, enabled=True)
        )
    except OrgApiKey.DoesNotExist:
        logger.warning("mcp_auth_failed", api_key_prefix=api_key[:8] if api_key else "")
        return None

    if org_api_key.type == "system":
        user = (
            User.objects.all()
            .select_related("organization")
            .filter(organization=org_api_key.organization)
            .order_by("created_at")
            .first()
        )
    else:
        user = org_api_key.user

    if not user:
        logger.warning(
            "mcp_auth_no_user",
            key_type=org_api_key.type,
            org=str(org_api_key.organization),
        )
        return None

    organization = org_api_key.organization
    workspace = (
        org_api_key.workspace
        or Workspace.objects.all()
        .filter(organization=organization, is_default=True, is_active=True)
        .first()
    )

    set_workspace_context(workspace=workspace, organization=organization, user=user)

    return MCPRequestContext(user=user, organization=organization, workspace=workspace)


def _authenticate_via_oauth(token: str) -> MCPRequestContext | None:
    """Authenticate via OAuth Bearer token and set per-request context."""
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from mcp_server.oauth_utils import decrypt_oauth_token

    payload = decrypt_oauth_token(token)
    if not payload or payload.get("type") != "mcp_oauth":
        return None

    try:
        user = (
            User.objects.all().select_related("organization").get(id=payload["user_id"])
        )
    except User.DoesNotExist:
        return None

    organization = user.organization

    workspace = None
    if payload.get("workspace_id"):
        workspace = (
            Workspace.objects.all()
            .filter(
                id=payload["workspace_id"], organization=organization, is_active=True
            )
            .first()
        )
    if not workspace:
        workspace = (
            Workspace.objects.all()
            .filter(organization=organization, is_default=True, is_active=True)
            .first()
        )

    set_workspace_context(workspace=workspace, organization=organization, user=user)

    return MCPRequestContext(user=user, organization=organization, workspace=workspace)


def _current_context() -> MCPRequestContext | None:
    organization = get_current_organization()
    workspace = get_current_workspace()
    user = get_current_user()
    if not organization or not user:
        return None
    return MCPRequestContext(user=user, organization=organization, workspace=workspace)


def _enabled_tools_for_context(context: MCPRequestContext):
    from mcp_server.usage_helpers import get_enabled_tools, get_or_create_connection

    connection = get_or_create_connection(
        context.user, context.organization, context.workspace
    )
    return connection, get_enabled_tools(connection)


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def _error_result(message: str, *, code: str, data=None) -> CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    if data is not None:
        payload["error"]["details"] = _json_safe(data)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


@mcp.list_tools()
async def list_generated_tools():
    context = _current_context()
    if context is None:
        return []
    _, enabled_names = await sync_to_async(_enabled_tools_for_context)(context)
    return [
        tool.to_mcp_tool() for tool in registry.list_all() if tool.name in enabled_names
    ]


@mcp.call_tool()
async def call_generated_tool(name: str, arguments: dict):
    context = _current_context()
    if context is None:
        return _error_result("Not authenticated", code="UNAUTHENTICATED")

    tool = registry.get(name)
    if tool is None:
        return _error_result(f"Tool not found: {name}", code="NOT_FOUND")

    from mcp_server.exceptions import RateLimitExceededError
    from mcp_server.rate_limiter import check_rate_limit, get_rate_limit_tier
    from mcp_server.usage_helpers import (
        get_or_create_session,
        record_usage,
        update_session_counters,
    )

    connection, enabled_names = await sync_to_async(_enabled_tools_for_context)(context)
    if name not in enabled_names:
        return _error_result(f"Tool is disabled: {name}", code="FORBIDDEN")

    try:
        tier = await sync_to_async(get_rate_limit_tier)(context.organization)
        await sync_to_async(check_rate_limit)(str(context.organization.id), tier)
    except RateLimitExceededError as exc:
        return _error_result(str(exc), code="RATE_LIMITED")

    session = await sync_to_async(get_or_create_session)(
        connection, transport="streamable_http"
    )
    started_at = time.time()
    try:
        data = await executor.execute(tool, arguments, context)
        payload = _json_safe(data if isinstance(data, dict) else {"result": data})
        is_error = False
        error_message = ""
        result = payload
    except APIExecutionError as exc:
        is_error = True
        error_message = str(exc)
        result = _error_result(str(exc), code=f"HTTP_{exc.status_code}", data=exc.data)
    except Exception as exc:
        logger.exception("mcp_generated_tool_failed", tool=name)
        is_error = True
        error_message = str(exc)
        result = _error_result(str(exc), code="INTERNAL_ERROR")

    latency_ms = int((time.time() - started_at) * 1000)
    try:
        await sync_to_async(record_usage)(
            session=session,
            tool_name=name,
            tool_group=tool.group,
            params=arguments,
            status="error" if is_error else "success",
            error_msg=error_message,
            latency_ms=latency_ms,
        )
        await sync_to_async(update_session_counters)(session, is_error)
    except Exception:
        logger.exception("usage_recording_failed", tool=name)
    return result


_streamable_app = None
_session_manager = None
_oauth_app = None


def get_mcp_oauth_app():
    """Get cached Starlette app with MCP OAuth routes.

    Creates auth routes (metadata, register, authorize, token, revoke) and
    protected resource metadata routes using the MCP SDK's built-in handlers,
    backed by our FutureAGIOAuthProvider.
    """
    global _oauth_app
    if _oauth_app is None:
        import os

        from mcp.server.auth.routes import (
            create_auth_routes,
            create_protected_resource_routes,
        )
        from mcp.server.auth.settings import (
            ClientRegistrationOptions,
            RevocationOptions,
        )
        from pydantic import AnyHttpUrl
        from starlette.applications import Starlette

        from mcp_server.constants import TOOL_GROUPS
        from mcp_server.oauth_provider import FutureAGIOAuthProvider

        base_url = os.environ.get("MCP_SERVER_BASE_URL", "http://localhost:8000")
        frontend_url = os.environ.get(
            "FRONTEND_URL",
            f"http://{os.environ.get('APP_URL', 'localhost:3031')}",
        )

        provider = FutureAGIOAuthProvider(frontend_url=frontend_url)

        auth_routes = create_auth_routes(
            provider=provider,
            issuer_url=AnyHttpUrl(base_url),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=list(TOOL_GROUPS.keys()),
                default_scopes=list(TOOL_GROUPS.keys()),
            ),
            revocation_options=RevocationOptions(enabled=True),
        )

        resource_routes = create_protected_resource_routes(
            resource_url=AnyHttpUrl(f"{base_url}/mcp"),
            authorization_servers=[AnyHttpUrl(base_url)],
            scopes_supported=list(TOOL_GROUPS.keys()),
        )

        _oauth_app = Starlette(routes=auth_routes + resource_routes)
    return _oauth_app


def get_mcp_streamable_app():
    """Get the cached MCP Streamable HTTP Starlette app.

    Returns a Starlette app with a single /mcp route.
    Stateless mode — no persistent sessions, survives restarts.
    """
    global _session_manager, _streamable_app
    if _streamable_app is None:
        _session_manager = StreamableHTTPSessionManager(
            app=mcp,
            event_store=None,
            json_response=True,
            stateless=True,
            security_settings=_transport_security,
        )

        class StreamableHTTPASGIApp:
            async def __call__(self, scope, receive, send):
                await _session_manager.handle_request(scope, receive, send)

        _streamable_app = Starlette(
            routes=[Route("/mcp", endpoint=StreamableHTTPASGIApp())],
            lifespan=lambda app: _session_manager.run(),
        )
    return _streamable_app


async def mcp_streamable_with_auth(scope, receive, send):
    """ASGI middleware that authenticates every request before delegating to MCP.

    Unlike SSE (which authenticated once on connect), Streamable HTTP is stateless
    so every POST /mcp request must carry credentials.

    Supports: Bearer token (OAuth) or X-Api-Key + X-Secret-Key headers.
    """
    import os

    from asgiref.sync import sync_to_async
    from starlette.responses import Response as StarletteResponse

    headers = dict(scope.get("headers", []))
    headers_str = {k.decode(): v.decode() for k, v in headers.items()}

    method = scope.get("method", "GET")

    # Build WWW-Authenticate header for 401 responses (RFC 9728)
    base_url = os.environ.get("MCP_SERVER_BASE_URL", "http://localhost:8000")
    www_auth = f'Bearer resource_metadata="{base_url}/.well-known/oauth-protected-resource/mcp"'

    # Authenticate every request (GET for session init, POST for tool calls, DELETE for cleanup)
    context = None

    # Try Bearer token first (OAuth)
    auth_header = headers_str.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        context = await sync_to_async(_authenticate_via_oauth)(token)
        if not context:
            response = StarletteResponse(
                content='{"error":"invalid_token","error_description":"Invalid or expired token"}',
                status_code=401,
                headers={
                    "Content-Type": "application/json",
                    "WWW-Authenticate": www_auth,
                },
            )
            await response(scope, receive, send)
            return

    # Fall back to API key auth
    if not context:
        api_key = headers_str.get("x-api-key", "")
        secret_key = headers_str.get("x-secret-key", "")

        if not api_key or not secret_key:
            response = StarletteResponse(
                content='{"error":"invalid_token","error_description":"Authentication required"}',
                status_code=401,
                headers={
                    "Content-Type": "application/json",
                    "WWW-Authenticate": www_auth,
                },
            )
            await response(scope, receive, send)
            return

        context = await sync_to_async(_authenticate_and_set_context)(
            api_key, secret_key
        )
        if not context:
            response = StarletteResponse(
                content='{"error":"invalid_token","error_description":"Invalid credentials"}',
                status_code=401,
                headers={
                    "Content-Type": "application/json",
                    "WWW-Authenticate": www_auth,
                },
            )
            await response(scope, receive, send)
            return

    logger.debug(
        "mcp_request_authenticated",
        method=method,
        org_id=str(context.organization.id),
        user=str(context.user.id),
    )

    # Delegate to MCP Streamable HTTP app
    # Session manager is started via ASGI lifespan in asgi.py
    app = get_mcp_streamable_app()
    await app(scope, receive, send)

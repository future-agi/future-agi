"""Python MCP server backed by OpenAPI-generated Django API tools.

Authentication: API key or OAuth Bearer token on each request.

Streamable HTTP uses a single /mcp endpoint (stateless) — no persistent connections,
no session affinity, survives server restarts, horizontally scalable.
"""

import json
import time
from contextvars import ContextVar

import structlog
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.conf import settings
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server.api_executor import (
    APIExecutionError,
    MCPRequestContext,
    ensure_urlconf_loaded,
    executor,
)
from mcp_server.generated_registry import registry
from mcp_server.public_url import mcp_public_base_url
from mcp_server.response_limits import ResponseTooLargeError, bounded_response
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)

logger = structlog.get_logger(__name__)

# The exact tenant each request authenticated as. Kept separately from the
# workspace context so the executor also receives the OrgApiKey that granted it.
_request_context: ContextVar[MCPRequestContext | None] = ContextVar(
    "mcp_request_context", default=None
)

# Build allowed hosts from the public MCP base URL (the one Settings > MCP
# Server shows) for DNS rebinding protection.
_mcp_base_url = mcp_public_base_url() or ""
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
    from rest_framework.exceptions import AuthenticationFailed

    from accounts.api_key_policy import APIKeyRetired, authenticate_api_key

    # The shared personal-key policy also refuses coding_agent keys: their
    # restricted simulate routes never include MCP tools.
    try:
        org_api_key = authenticate_api_key(api_key, secret_key)
    except APIKeyRetired:
        raise
    except AuthenticationFailed:
        logger.warning("mcp_auth_failed")
        return None
    user = org_api_key.user

    organization = org_api_key.organization
    workspace = org_api_key.workspace
    if workspace is None:
        logger.warning("mcp_auth_failed")
        return None

    return _bind_context(
        MCPRequestContext(
            user=user,
            organization=organization,
            workspace=workspace,
            api_key=org_api_key,
        )
    )


def _authenticate_via_oauth(token: str) -> MCPRequestContext | None:
    """Bind the bearer's exact owner, tenant and immutable tool-group grant."""
    from uuid import UUID

    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from mcp_server.models.oauth_revocation import MCPOAuthAccessRevocation
    from mcp_server.oauth_utils import decrypt_oauth_token

    payload = decrypt_oauth_token(token)
    if not isinstance(payload, dict) or payload.get("type") != "mcp_oauth":
        return None
    if MCPOAuthAccessRevocation.is_revoked(token):
        return None
    # SDK grants use a list; older tokens can carry space-delimited scopes.
    # Missing or malformed scopes must never become an unrestricted grant.
    scopes = payload.get("scope")
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, list) or any(
        not isinstance(group, str) for group in scopes
    ):
        return None
    granted_tool_groups = frozenset(scopes)
    try:
        user_id = UUID(str(payload.get("user_id")))
        organization_id = UUID(str(payload.get("org_id")))
        workspace_id = UUID(str(payload.get("workspace_id")))
    except (ValueError, TypeError, AttributeError):
        return None
    user = (
        User.objects.all()
        .select_related("organization")
        .filter(pk=user_id, is_active=True)
        .first()
    )
    if user is None:
        return None

    # A live membership in the grant's organization is authoritative, not the
    # user's home organization or another membership selected by preference.
    organization = Organization.objects.filter(pk=organization_id).first()
    if (
        organization is None
        or not OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization, is_active=True, deleted=False
        ).exists()
    ):
        return None

    # Missing, inactive and foreign workspaces never fall back to a default.
    # Keep IR's live workspace-access check in addition to exact grant binding.
    workspace = Workspace.no_workspace_objects.filter(
        pk=workspace_id, organization=organization, is_active=True
    ).first()
    if workspace is None or not user.can_access_workspace(workspace):
        return None

    return _bind_context(
        MCPRequestContext(
            user=user,
            organization=organization,
            workspace=workspace,
            granted_tool_groups=granted_tool_groups,
        )
    )


def _bind_context(context: MCPRequestContext) -> MCPRequestContext:
    set_workspace_context(
        workspace=context.workspace,
        organization=context.organization,
        user=context.user,
        require_workspace=True,
    )
    _request_context.set(context)
    return context


def _clear_context() -> None:
    _request_context.set(None)
    clear_workspace_context()


def _current_context() -> MCPRequestContext | None:
    context = _request_context.get()
    if context is not None:
        return context
    organization = get_current_organization()
    workspace = get_current_workspace()
    user = get_current_user()
    if not organization or not user:
        return None
    return MCPRequestContext(user=user, organization=organization, workspace=workspace)


def _enabled_tools_for_context(context: MCPRequestContext, *, create=False):
    from mcp_server.usage_helpers import (
        get_connection,
        get_enabled_tools,
        get_or_create_connection,
    )
    from tfc.utils.tenant_scope import acts_in_organization

    # Discovery is read-only; only tool calls may initialize a scoped connection.
    if (
        context.user is None
        or not context.user.is_authenticated
        or not context.user.is_active
        or context.organization is None
        or context.workspace is None
        or context.workspace.organization_id != context.organization.pk
    ):
        return None, set()
    # Preserve IR's live tenant recheck, including revocation after binding.
    if not acts_in_organization(context.user, context.organization):
        return None, set()
    lookup = get_or_create_connection if create else get_connection
    connection = lookup(context.user, context.organization, context.workspace)
    enabled_names = get_enabled_tools(connection)
    if context.granted_tool_groups is not None:
        # Shared connection policy may narrow a bearer grant, never expand it.
        # Both discovery and execution use this same intersection.
        enabled_names &= {
            tool.name for tool in registry.list_by_groups(context.granted_tool_groups)
        }
    return connection, enabled_names


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def _error_result(message: str, *, code: str, data=None) -> CallToolResult:
    payload = {"error": {"code": code, "message": message[:2000]}}
    if data is not None:
        try:
            payload["error"]["details"] = bounded_response(_json_safe(data))
        except ResponseTooLargeError:
            payload["error"]["details"] = "Error details exceeded the MCP size limit."
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))
        ],
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


# The executor validates with a cached validator on its Django worker thread.
@mcp.call_tool(validate_input=False)
async def call_generated_tool(name: str, arguments: dict):
    context = _current_context()
    if context is None:
        return _error_result("Not authenticated", code="UNAUTHENTICATED")

    tool = registry.get(name)
    if tool is None:
        return _error_result(f"Tool not found: {name}", code="NOT_FOUND")

    from rest_framework.exceptions import PermissionDenied

    from mcp_server.exceptions import RateLimitExceededError
    from mcp_server.rate_limiter import check_rate_limit, get_rate_limit_tier
    from mcp_server.usage_helpers import (
        get_or_create_session,
        record_usage,
        update_session_counters,
    )

    try:
        connection, enabled_names = await sync_to_async(_enabled_tools_for_context)(
            context, create=True
        )
    except PermissionDenied:
        return _error_result("Invalid MCP connection scope", code="HTTP_403")
    if connection is None:
        # The same refusal the executor gives a caller outside the tenant.
        return _error_result("Access denied to this organization", code="HTTP_403")
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
        result = CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))
            ],
            structuredContent=payload,
            isError=False,
        )
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
        from mcp_server.oauth_routes import revocation_route

        # The same base the dashboard shows as "<base>/mcp", so the issuer and
        # the protected resource match the URL an IDE was given.
        base_url = mcp_public_base_url() or "http://localhost:8000"

        provider = FutureAGIOAuthProvider()

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

        auth_routes = [
            revocation_route(provider) if route.path == "/revoke" else route
            for route in auth_routes
        ]

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
            # MCP 1.30 defaults this to 4 MiB, which 413s tool calls carrying
            # inline base64 media (e.g. add_dataset_rows cells) that the rest
            # of the ingress path already accepts; match the same ceiling
            # Django and nginx enforce everywhere else (DATA_UPLOAD_MAX_MEMORY_SIZE).
            max_request_body_size=settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
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
    """Keep each MCP request's synchronous work off other clients' executor."""
    from django.db import close_old_connections

    async with ThreadSensitiveContext():
        _clear_context()
        await sync_to_async(close_old_connections)()
        # Import view modules before this request's workspace is bound; see
        # ensure_urlconf_loaded for why the order matters on a cold worker.
        await sync_to_async(ensure_urlconf_loaded)()
        try:
            await _mcp_streamable_with_auth(scope, receive, send)
        finally:
            _clear_context()
            await sync_to_async(close_old_connections)()


async def _mcp_streamable_with_auth(scope, receive, send):
    """ASGI middleware that authenticates every request before delegating to MCP.

    Unlike SSE (which authenticated once on connect), Streamable HTTP is stateless
    so every POST /mcp request must carry credentials.

    Supports: Bearer token (OAuth) or X-Api-Key + X-Secret-Key headers.
    """
    from asgiref.sync import sync_to_async
    from starlette.responses import Response as StarletteResponse

    headers = dict(scope.get("headers", []))
    headers_str = {k.decode(): v.decode() for k, v in headers.items()}

    method = scope.get("method", "GET")

    # Build WWW-Authenticate header for 401 responses (RFC 9728), from the
    # same public base the dashboard shows.
    base_url = mcp_public_base_url() or "http://localhost:8000"
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

        from accounts.api_key_policy import APIKeyRetired

        try:
            context = await sync_to_async(_authenticate_and_set_context)(
                api_key, secret_key
            )
        except APIKeyRetired as exc:
            response = StarletteResponse(
                content=json.dumps(
                    {"code": exc.default_code, "create_url": exc.create_url}
                ),
                status_code=401,
                headers={
                    "Content-Type": "application/json",
                    "WWW-Authenticate": www_auth,
                },
            )
            await response(scope, receive, send)
            return
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
        org_id=str(context.organization.id) if context.organization else None,
        user=str(context.user.id),
    )

    # Delegate to MCP Streamable HTTP app
    # Session manager is started via ASGI lifespan in asgi.py
    app = get_mcp_streamable_app()
    await app(scope, receive, send)

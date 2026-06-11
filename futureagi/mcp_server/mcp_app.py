"""MCP Server application that wraps ai_tools as MCP tools.

Creates a FastMCP server exposing all registered ai_tools via Streamable HTTP transport.
Authentication: API key or OAuth Bearer token on each request.

Streamable HTTP uses a single /mcp endpoint (stateless) — no persistent connections,
no session affinity, survives server restarts, horizontally scalable.
"""

import os
import time

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ai_tools.base import ToolContext
from ai_tools.registry import registry as ai_registry
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)

logger = structlog.get_logger(__name__)

# Build allowed hosts from MCP_SERVER_BASE_URL for DNS rebinding protection.
_mcp_base_url = os.environ.get("MCP_SERVER_BASE_URL", "")
_allowed_hosts: list[str] = []
if _mcp_base_url:
    from urllib.parse import urlparse

    _parsed = urlparse(_mcp_base_url)
    if _parsed.hostname:
        _allowed_hosts.append(_parsed.hostname)
        if _parsed.port:
            _allowed_hosts.append(f"{_parsed.hostname}:{_parsed.port}")

# Create the MCP server (stateless Streamable HTTP)
mcp = FastMCP(
    name="Future AGI",
    instructions=(
        "You are connected to the Future AGI platform. "
        "Use the available tools to explore evaluations, datasets, traces, "
        "and other resources in your workspace."
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
    ),
)


def _authenticate_and_set_context(api_key: str, secret_key: str) -> ToolContext | None:
    """Authenticate via API key and set per-request context."""
    from accounts.models.user import OrgApiKey

    from mcp_server.org_resolution import (
        WorkspaceAccessDenied,
        resolve_accessible_workspace,
        resolve_system_key_principal,
    )

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
        # Phase 7A seam S4: a deliberate principal — the org's longest-
        # standing ACTIVE owner-level member — never an arbitrary (possibly
        # revoked) User row.
        user = resolve_system_key_principal(org_api_key.organization)
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
    # Phase 7A seam S3: every workspace bind (explicit key workspace OR the
    # default-workspace fallback) is access-verified; inaccessible ⇒ reject.
    try:
        workspace = resolve_accessible_workspace(
            user,
            organization,
            workspace_id=org_api_key.workspace_id,
        )
    except WorkspaceAccessDenied:
        logger.warning(
            "mcp_auth_workspace_denied",
            key_type=org_api_key.type,
            user_id=str(user.id),
            org=str(organization.id),
        )
        return None

    set_workspace_context(workspace=workspace, organization=organization, user=user)

    return ToolContext(
        user=user, organization=organization, workspace=workspace, transport="mcp"
    )


def _authenticate_via_oauth(token: str) -> ToolContext | None:
    """Authenticate via OAuth Bearer token and set per-request context.

    Phase 7A seam S1: the org encrypted into the token at approval
    (``oauth_utils.generate_oauth_token``) is the binding authority — NOT the
    legacy ``user.organization`` FK — and an ACTIVE ``OrganizationMembership``
    is re-verified on every request, so removing a member revokes effective
    access without waiting for token expiry. Mismatch/revoked ⇒ ``None`` ⇒
    the transport answers with a clean 401 ``invalid_token`` MCP error.
    """
    from accounts.models.user import User
    from mcp_server.oauth_utils import decrypt_oauth_token
    from mcp_server.org_resolution import (
        WorkspaceAccessDenied,
        resolve_accessible_workspace,
        resolve_membership_org,
    )

    payload = decrypt_oauth_token(token)
    if not payload or payload.get("type") != "mcp_oauth":
        return None

    try:
        user = (
            User.objects.all().select_related("organization").get(id=payload["user_id"])
        )
    except User.DoesNotExist:
        return None

    if not user.is_active:
        logger.warning("mcp_oauth_inactive_user", user_id=str(user.id))
        return None

    organization = resolve_membership_org(user, org_id=payload.get("org_id"))
    if organization is None:
        logger.warning(
            "mcp_oauth_org_binding_rejected",
            user_id=str(user.id),
            token_org_id=str(payload.get("org_id")),
        )
        return None

    # Phase 7A seam S3: token workspace verified (in-org + access); the
    # default-workspace fallback is access-verified too — a workspace-
    # restricted user binds their own workspace or is rejected.
    try:
        workspace = resolve_accessible_workspace(
            user, organization, workspace_id=payload.get("workspace_id")
        )
    except WorkspaceAccessDenied:
        logger.warning(
            "mcp_oauth_workspace_denied",
            user_id=str(user.id),
            workspace_id=str(payload.get("workspace_id")),
        )
        return None

    set_workspace_context(workspace=workspace, organization=organization, user=user)

    return ToolContext(
        user=user, organization=organization, workspace=workspace, transport="mcp"
    )


def _register_ai_tools():
    """Register all ai_tools as MCP tools on the FastMCP server.

    Each tool handler gets a proper ``__signature__`` derived from the tool's
    Pydantic input_model so that FastMCP generates the correct JSON Schema
    for MCP clients.  At runtime, ``**kwargs`` still captures the actual
    arguments and passes them through to ``tool.run()``.
    """
    import inspect

    from asgiref.sync import sync_to_async

    for tool in ai_registry.list_all():

        def make_handler(t):
            def _sync_run(kwargs_dict):
                from mcp_server.constants import CATEGORY_TO_GROUP
                from mcp_server.exceptions import RateLimitExceededError
                from mcp_server.rate_limiter import (
                    check_rate_limit,
                    get_rate_limit_tier,
                )
                from mcp_server.usage_helpers import (
                    record_usage,
                    update_session_counters,
                )

                org = get_current_organization()
                ws = get_current_workspace()
                usr = get_current_user()

                # If context vars not set (OAuth flow), try auth context
                if not org or not usr:
                    try:
                        from mcp.server.auth.middleware.auth_context import (
                            get_access_token,
                        )

                        from mcp_server.oauth_provider import FutureAGIAccessToken

                        access = get_access_token()
                        if isinstance(access, FutureAGIAccessToken):
                            from accounts.models.user import User

                            from mcp_server.org_resolution import (
                                resolve_accessible_workspace,
                                resolve_membership_org,
                            )

                            usr = (
                                User.objects.all()
                                .select_related("organization")
                                .get(id=access.user_id)
                            )
                            # Phase 7A seam S1: bind the token's org with an
                            # active-membership check — never the legacy FK.
                            # Seam S3: the workspace lookup is org-scoped and
                            # access-verified (the old lookup didn't even
                            # filter by org). WorkspaceAccessDenied propagates
                            # to the generic except ⇒ unauthenticated.
                            org = resolve_membership_org(
                                usr, org_id=access.organization_id
                            )
                            if org is None:
                                usr = None
                            else:
                                ws = resolve_accessible_workspace(
                                    usr, org, workspace_id=access.workspace_id
                                )
                                set_workspace_context(
                                    workspace=ws, organization=org, user=usr
                                )
                    except Exception:
                        usr = None
                        org = None

                if not org or not usr:
                    return "Error: Not authenticated."

                # Rate limit check
                tier = get_rate_limit_tier(org)
                try:
                    check_rate_limit(str(org.id), tier)
                except RateLimitExceededError as e:
                    return f"Error: {e} (retry after {e.retry_after}s)"

                context = ToolContext(
                    user=usr, organization=org, workspace=ws, transport="mcp"
                )

                start = time.time()
                result = t.run(kwargs_dict, context)
                latency_ms = int((time.time() - start) * 1000)

                # Record usage (get or create a session for this user)
                try:
                    from mcp_server.usage_helpers import (
                        get_or_create_connection,
                        get_or_create_session,
                    )

                    connection = get_or_create_connection(usr, org, ws)
                    session = get_or_create_session(
                        connection, transport="streamable_http"
                    )
                    tool_group = CATEGORY_TO_GROUP.get(t.category, "")
                    is_error = result.is_error
                    record_usage(
                        session=session,
                        tool_name=t.name,
                        tool_group=tool_group,
                        params=kwargs_dict,
                        status="error" if is_error else "success",
                        error_msg=result.content if is_error else "",
                        latency_ms=latency_ms,
                    )
                    update_session_counters(session, is_error)
                except Exception:
                    logger.exception("usage_recording_failed", tool=t.name)

                if result.is_error:
                    code = result.error_code or "INTERNAL_ERROR"
                    return f"Error [{code}]: {result.content}"
                return result.content

            async def handler(**kwargs) -> str:
                return await sync_to_async(_sync_run)(kwargs)

            handler.__name__ = t.name
            handler.__doc__ = t.description

            # Build a proper inspect.Signature from the tool's Pydantic model
            # so FastMCP generates the correct input schema for MCP clients.
            params = []
            for field_name, field_info in t.input_model.model_fields.items():
                default = (
                    field_info.default
                    if field_info.default is not None
                    else inspect.Parameter.empty
                )
                if not field_info.is_required():
                    default = field_info.default
                else:
                    default = inspect.Parameter.empty

                params.append(
                    inspect.Parameter(
                        field_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        default=default,
                        annotation=field_info.annotation,
                    )
                )

            # Phase 3A: destructive tools take an extra optional `confirm`
            # bool (popped before validation in BaseTool.run). Without it in
            # the signature, FastMCP would reject the phase-2 call.
            if getattr(t, "execution_policy", "") == "destructive":
                params.append(
                    inspect.Parameter(
                        "confirm",
                        inspect.Parameter.KEYWORD_ONLY,
                        default=False,
                        annotation=bool,
                    )
                )

            handler.__signature__ = inspect.Signature(params, return_annotation=str)

            return handler

        mcp.tool(name=tool.name, description=tool.description)(make_handler(tool))


_register_ai_tools()


_streamable_app = None
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
    global _streamable_app
    if _streamable_app is None:
        _streamable_app = mcp.streamable_http_app()
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

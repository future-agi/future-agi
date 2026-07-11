"""Internal API endpoints for MCP tool calls (used by stdio proxy and direct API)."""

import time

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_tools.base import ToolContext
from ai_tools.registry import registry
from mcp_server.constants import CATEGORY_TO_GROUP
from mcp_server.exceptions import RateLimitExceededError
from mcp_server.rate_limiter import check_rate_limit, get_rate_limit_tier
from mcp_server.serializers.contracts import (
    MCPErrorResponseSerializer,
    MCPToolCallRequestSerializer,
    MCPToolCallResponseSerializer,
    MCPToolListResponseSerializer,
)
from mcp_server.usage_helpers import (
    get_enabled_tools,
    get_or_create_connection,
    get_or_create_session,
    record_usage,
    update_session_counters,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)


class MCPToolCallView(APIView):
    """Execute a tool call via internal API (used by stdio proxy)."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=MCPToolCallRequestSerializer,
        responses={
            200: MCPToolCallResponseSerializer,
            400: MCPErrorResponseSerializer,
            403: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
            429: MCPErrorResponseSerializer,
            500: MCPErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        tool_name = request.validated_data["tool_name"]
        params = request.validated_data.get("params", {})
        session_id = request.validated_data.get("session_id")

        user = request.user
        # Phase 7A seam S2: no stale-FK fallback. When the auth layer didn't
        # bind an org (request.organization is None), resolve via ACTIVE
        # membership with accounts/authentication.py::_resolve_organization
        # semantics (legacy FK only for accounts with zero membership rows).
        # A user revoked from every org gets 403, not their old FK org.
        organization = getattr(request, "organization", None)
        if organization is None:
            from mcp_server.org_resolution import resolve_membership_org

            organization = resolve_membership_org(user)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        # Get tool from registry
        tool = registry.get(tool_name)
        if not tool:
            return Response(
                build_error_envelope(f"Tool not found: {tool_name}", status_code=404),
                status=404,
            )

        # Get or create connection + session
        connection = get_or_create_connection(user, organization, workspace)
        session = get_or_create_session(connection, session_id)

        # Rate limit check
        tier = get_rate_limit_tier(organization)
        try:
            check_rate_limit(str(organization.id), tier)
        except RateLimitExceededError as e:
            return Response(
                build_error_envelope(
                    str(e),
                    status_code=429,
                    extra={"retry_after": e.retry_after},
                ),
                status=429,
                headers={"Retry-After": str(e.retry_after)},
            )

        # Check tool is enabled
        enabled_tools = get_enabled_tools(connection)
        if tool_name not in enabled_tools:
            return Response(
                build_error_envelope(
                    f"Tool is disabled: {tool_name}",
                    status_code=403,
                ),
                status=403,
            )

        # Build context and execute
        context = ToolContext(
            user=user,
            organization=organization,
            workspace=workspace,
            # Phase 3A: the MCP client is the (human-operated) approver for
            # destructive tools — preview-first still enforced by the gate.
            transport="mcp",
        )

        start_time = time.time()
        try:
            result = tool.run(params, context)
            latency_ms = int((time.time() - start_time) * 1000)

            # Update session counters
            update_session_counters(session, result.is_error)

            # Record usage
            tool_group = CATEGORY_TO_GROUP.get(tool.category, "")
            record_usage(
                session=session,
                tool_name=tool_name,
                tool_group=tool_group,
                params=params,
                status="error" if result.is_error else "success",
                error_msg=result.content if result.is_error else "",
                latency_ms=latency_ms,
            )

            return Response(
                {
                    "status": not result.is_error,
                    "result": {
                        "content": result.content,
                        "data": result.data,
                        "is_error": result.is_error,
                        "error_code": result.error_code,
                    },
                    "session_id": str(session.id),
                }
            )
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error("mcp_tool_call_error", tool=tool_name, error=str(e))

            update_session_counters(session, is_error=True)

            tool_group = CATEGORY_TO_GROUP.get(tool.category, "")
            record_usage(
                session=session,
                tool_name=tool_name,
                tool_group=tool_group,
                params=params,
                status="error",
                error_msg=str(e),
                latency_ms=latency_ms,
            )

            return Response(
                build_error_envelope(str(e), status_code=500),
                status=500,
            )


class MCPToolListView(APIView):
    """List available tools for the authenticated user."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPToolListResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        user = request.user
        # Phase 7A seam S2: same membership-verified resolution as
        # MCPToolCallView — never the stale user.organization FK.
        organization = getattr(request, "organization", None)
        if organization is None:
            from mcp_server.org_resolution import resolve_membership_org

            organization = resolve_membership_org(user)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        connection = get_or_create_connection(user, organization, workspace)
        enabled_tools = get_enabled_tools(connection)

        tools = []
        for tool in registry.list_all():
            if tool.name in enabled_tools:
                tools.append(tool.to_dict())

        return Response(
            {
                "status": True,
                "result": {
                    "tools": tools,
                    "total": len(tools),
                    "session_id": None,
                },
            }
        )

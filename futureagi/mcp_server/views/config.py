"""Dashboard API endpoints for MCP configuration."""

from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.constants import DEFAULT_TOOL_GROUPS, TOOL_GROUPS
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.public_url import mcp_public_url
from mcp_server.serializers.connection import (
    MCPConnectionSerializer,
    MCPConnectionUpdateSerializer,
)
from mcp_server.serializers.contracts import (
    MCPConnectionResponseSerializer,
    MCPErrorResponseSerializer,
    MCPToolGroupsResponseSerializer,
)
from mcp_server.serializers.tool_config import MCPToolGroupConfigUpdateSerializer
from mcp_server.usage_helpers import (
    get_connection,
    get_or_create_connection,
    require_connection_scope,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope


class MCPConfigView(APIView):
    """Get or update MCP connection configuration."""

    permission_classes = [IsAuthenticated]

    def _get_mcp_url(self):
        """The public MCP endpoint URL, the same base the MCP server's OAuth
        metadata and 401 advertise (mcp_server.public_url)."""
        return mcp_public_url()

    @swagger_auto_schema(
        responses={
            200: MCPConnectionResponseSerializer,
            403: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        user = request.user
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        require_connection_scope(user, organization, workspace)
        connection = get_connection(user, organization, workspace)
        if connection is None:
            # Dashboard configuration initializes an owner's default, unlike tool
            # discovery. The scoped writer must still refuse conflicting rows.
            try:
                connection = get_or_create_connection(
                    user, organization, workspace, connection_mode="remote"
                )
            except PermissionDenied:
                return Response(
                    build_error_envelope("No MCP connection found", status_code=404),
                    status=404,
                )

        serializer = MCPConnectionSerializer(connection)
        result = serializer.data
        result["mcp_url"] = self._get_mcp_url()
        return Response({"status": True, "result": result})

    @validated_request(
        request_serializer=MCPConnectionUpdateSerializer,
        responses={
            200: MCPConnectionResponseSerializer,
            403: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
        },
        reject_unknown_fields=True,
        partial_request_validation=True,
    )
    def put(self, request):
        user = request.user
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        connection = get_or_create_connection(
            user, organization, workspace, connection_mode="remote"
        )

        serializer = MCPConnectionUpdateSerializer(
            connection, data=request.validated_data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"status": True, "result": MCPConnectionSerializer(connection).data}
        )


class MCPToolGroupsView(APIView):
    """Get or update tool group configuration."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPToolGroupsResponseSerializer,
            403: MCPErrorResponseSerializer,
            500: MCPErrorResponseSerializer,
        }
    )
    def get(self, request):
        user = request.user
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        connection = get_connection(user, organization, workspace)
        config = None
        if connection is not None:
            try:
                config = connection.tool_config
            except MCPToolGroupConfig.DoesNotExist:
                pass
        if config is None:
            return Response(
                {
                    "status": True,
                    "result": {
                        "enabled_groups": DEFAULT_TOOL_GROUPS,
                        "disabled_tools": [],
                        "available_groups": [
                            {
                                "slug": slug,
                                "name": meta["name"],
                                "description": meta["description"],
                                "enabled": slug in DEFAULT_TOOL_GROUPS,
                            }
                            for slug, meta in TOOL_GROUPS.items()
                        ],
                    },
                }
            )

        from mcp_server.serializers.tool_config import MCPToolGroupConfigSerializer

        serializer = MCPToolGroupConfigSerializer(config)
        return Response({"status": True, "result": serializer.data})

    @validated_request(
        request_serializer=MCPToolGroupConfigUpdateSerializer,
        responses={
            200: MCPToolGroupsResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def put(self, request):
        user = request.user
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        connection = get_or_create_connection(
            user, organization, workspace, connection_mode="remote"
        )

        config, _ = MCPToolGroupConfig.no_workspace_objects.get_or_create(
            connection=connection,
        )

        if "enabled_groups" in request.validated_data:
            config.enabled_groups = request.validated_data["enabled_groups"]
        if "disabled_tools" in request.validated_data:
            config.disabled_tools = request.validated_data["disabled_tools"]
        config.save()

        from mcp_server.serializers.tool_config import MCPToolGroupConfigSerializer

        return Response(
            {"status": True, "result": MCPToolGroupConfigSerializer(config).data}
        )

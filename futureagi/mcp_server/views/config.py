"""Dashboard API endpoints for MCP configuration."""

from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.constants import DEFAULT_TOOL_GROUPS, TOOL_GROUPS
from mcp_server.models.connection import MCPConnection
from mcp_server.models.tool_config import MCPToolGroupConfig
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
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope


class MCPConfigView(APIView):
    """Get or update MCP connection configuration."""

    permission_classes = [IsAuthenticated]

    def _get_mcp_url(self):
        """Build the public MCP endpoint URL."""
        host = getattr(settings, "MCP_SERVER_HOST", None) or getattr(
            settings, "BASE_URL", ""
        )
        return f"{host}/mcp" if host else None

    @swagger_auto_schema(
        responses={
            200: MCPConnectionResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        user = request.user
        organization = request.organization
        workspace = request.workspace

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        try:
            connection = MCPConnection.no_workspace_objects.get(
                user=user,
                workspace=workspace,
                deleted=False,
            )
        except MCPConnection.DoesNotExist:
            connection = MCPConnection(
                user=user,
                organization=organization,
                workspace=workspace,
            )
            connection.save()
            MCPToolGroupConfig(connection=connection).save()

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
        organization = request.organization
        workspace = request.workspace

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        try:
            connection = MCPConnection.no_workspace_objects.get(
                user=user,
                workspace=workspace,
                deleted=False,
            )
        except MCPConnection.DoesNotExist:
            return Response(
                build_error_envelope("No MCP connection found", status_code=404),
                status=404,
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
        workspace = request.workspace

        try:
            connection = MCPConnection.no_workspace_objects.get(
                user=user,
                workspace=workspace,
                deleted=False,
            )
            config = connection.tool_config
        except (MCPConnection.DoesNotExist, MCPToolGroupConfig.DoesNotExist):
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
        organization = request.organization
        workspace = request.workspace

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        try:
            connection = MCPConnection.no_workspace_objects.get(
                user=user,
                workspace=workspace,
                deleted=False,
            )
        except MCPConnection.DoesNotExist:
            connection = MCPConnection(
                user=user,
                organization=organization,
                workspace=workspace,
            )
            connection.save()

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

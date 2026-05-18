"""Dashboard API endpoints for MCP configuration."""

from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
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


class MCPConfigView(APIView):
    """Get or update MCP connection configuration."""

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
                {"status": False, "error": "No organization context"}, status=403
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

    @swagger_auto_schema(
        request_body=MCPConnectionUpdateSerializer,
        responses={
            200: MCPConnectionResponseSerializer,
            403: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
        },
    )
    def put(self, request):
        user = request.user
        organization = request.organization
        workspace = request.workspace

        if not organization:
            return Response(
                {"status": False, "error": "No organization context"}, status=403
            )

        try:
            connection = MCPConnection.no_workspace_objects.get(
                user=user,
                workspace=workspace,
                deleted=False,
            )
        except MCPConnection.DoesNotExist:
            return Response(
                {"status": False, "error": "No MCP connection found"}, status=404
            )

        serializer = MCPConnectionUpdateSerializer(
            connection, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"status": True, "result": MCPConnectionSerializer(connection).data}
        )


class MCPToolGroupsView(APIView):
    """Get or update tool group configuration."""

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

    @swagger_auto_schema(
        request_body=MCPToolGroupConfigUpdateSerializer,
        responses={
            200: MCPToolGroupsResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def put(self, request):
        user = request.user
        organization = request.organization
        workspace = request.workspace

        if not organization:
            return Response(
                {"status": False, "error": "No organization context"}, status=403
            )

        serializer = MCPToolGroupConfigUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

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

        if "enabled_groups" in serializer.validated_data:
            config.enabled_groups = serializer.validated_data["enabled_groups"]
        if "disabled_tools" in serializer.validated_data:
            config.disabled_tools = serializer.validated_data["disabled_tools"]
        config.save()

        from mcp_server.serializers.tool_config import MCPToolGroupConfigSerializer

        return Response(
            {"status": True, "result": MCPToolGroupConfigSerializer(config).data}
        )

from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_tools.registry import registry
from mcp_server.serializers.contracts import (
    MCPErrorResponseSerializer,
    MCPHealthResponseSerializer,
)


class MCPHealthView(APIView):
    """Unauthenticated health check for MCP server."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        responses={
            200: MCPHealthResponseSerializer,
            500: MCPErrorResponseSerializer,
        }
    )
    def get(self, request):
        return Response(
            {
                "status": True,
                "result": {
                    "healthy": True,
                    "tool_count": registry.count(),
                    "version": "1.0.0",
                },
            }
        )

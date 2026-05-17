from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_tools.registry import registry
from ai_tools.serializers import ToolDiscoveryResponseSerializer


class ToolDiscoveryView(APIView):
    """Lists all registered AI tools for discovery and debugging."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: ToolDiscoveryResponseSerializer})
    def get(self, request):
        category = request.query_params.get("category")

        if category:
            tools = registry.list_by_category(category)
        else:
            tools = registry.list_all()

        return Response(
            {
                "status": True,
                "result": {
                    "tools": [tool.to_dict() for tool in tools],
                    "categories": registry.categories(),
                    "total": len(tools),
                },
            }
        )

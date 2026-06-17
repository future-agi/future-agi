"""Dashboard API endpoints for MCP sessions."""

from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.models.session import MCPSession
from mcp_server.serializers.contracts import (
    MCPErrorResponseSerializer,
    MCPSessionListResponseSerializer,
    MCPSessionRevokeResponseSerializer,
)
from mcp_server.serializers.session import MCPSessionSerializer
from tfc.utils.api_errors import build_error_envelope


class MCPSessionListView(APIView):
    """List active and recent MCP sessions."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPSessionListResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        user = request.user
        organization = getattr(request, "organization", None) or getattr(
            user, "organization", None
        )

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        status_filter = request.query_params.get("status")
        qs = MCPSession.objects.filter(
            organization=organization,
        )

        if status_filter:
            qs = qs.filter(status=status_filter)

        qs = qs.order_by("-started_at")[:50]

        serializer = MCPSessionSerializer(qs, many=True)
        return Response({"status": True, "result": serializer.data})


class MCPSessionDetailView(APIView):
    """Revoke a specific MCP session."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPSessionRevokeResponseSerializer,
            403: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
            500: MCPErrorResponseSerializer,
        }
    )
    def delete(self, request, session_id):
        organization = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        try:
            session = MCPSession.objects.get(
                id=session_id,
                organization=organization,
            )
        except MCPSession.DoesNotExist:
            return Response(
                build_error_envelope("Session not found", status_code=404),
                status=404,
            )

        session.status = "revoked"
        session.ended_at = timezone.now()
        session.save(update_fields=["status", "ended_at"])

        return Response({"status": True, "result": {"message": "Session revoked"}})

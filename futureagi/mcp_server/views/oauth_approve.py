"""OAuth 2.0 approval endpoints for the MCP SDK OAuth flow.

These DRF views are used by the frontend consent page when a user needs
to approve an MCP OAuth authorization request (production mode).
"""

import secrets
import time

import structlog
from django.conf import settings
from django.core.cache import cache
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.constants import TOOL_GROUPS
from mcp_server.oauth_provider import (
    APPROVE_PREFIX,
    CODE_PREFIX,
    CODE_TTL,
    FutureAGIAuthorizationCode,
)
from mcp_server.serializers.contracts import (
    MCPErrorResponseSerializer,
    MCPOAuthApproveInfoResponseSerializer,
    MCPOAuthApproveRequestSerializer,
    MCPOAuthRedirectResponseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)


class MCPOAuthApproveInfoView(APIView):
    """GET /mcp/oauth/approve-info/ - Get approval request details.

    Called by the frontend consent page to display what the client is requesting.
    Public endpoint - no auth required (just shows what's being requested).
    """

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        responses={
            200: MCPOAuthApproveInfoResponseSerializer,
            400: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        request_id = request.query_params.get("request_id")
        if not request_id:
            return Response(
                build_error_envelope("Missing request_id", status_code=400),
                status=400,
            )

        data = cache.get(f"{APPROVE_PREFIX}{request_id}")
        if data is None:
            return Response(
                build_error_envelope(
                    "Approval request not found or expired",
                    status_code=404,
                ),
                status=404,
            )

        # Build available tool groups with checked status
        requested_scopes = data.get("scopes", [])
        available_groups = []
        for slug, meta in TOOL_GROUPS.items():
            available_groups.append(
                {
                    "slug": slug,
                    "name": meta["name"],
                    "description": meta["description"],
                    "checked": slug in requested_scopes if requested_scopes else True,
                }
            )

        return Response(
            {
                "status": True,
                "result": {
                    "client_name": data.get(
                        "client_name", data.get("client_id", "Unknown")
                    ),
                    "client_id": data.get("client_id"),
                    "scopes": requested_scopes,
                    "redirect_uri": data.get("redirect_uri"),
                    "available_groups": available_groups,
                },
            }
        )


class MCPOAuthApproveView(APIView):
    """POST /mcp/oauth/approve/ - Process user approval decision.

    Called by the frontend consent page when the user approves or denies.
    Requires JWT auth (authenticated user).
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=MCPOAuthApproveRequestSerializer,
        responses={
            200: MCPOAuthRedirectResponseSerializer,
            400: MCPErrorResponseSerializer,
            403: MCPErrorResponseSerializer,
            404: MCPErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        user = request.user
        organization = getattr(request, "organization", None) or getattr(
            settings, "ORGANIZATION", None
        )
        workspace = getattr(request, "workspace", None) or getattr(
            settings, "CURRENT_WORKSPACE", None
        )

        request_id = request.validated_data["request_id"]
        approved = request.validated_data.get("approved", False)
        selected_groups = request.validated_data.get("selected_groups", [])

        if not organization:
            return Response(
                build_error_envelope("No organization context", status_code=403),
                status=403,
            )

        data = cache.get(f"{APPROVE_PREFIX}{request_id}")
        if data is None:
            return Response(
                build_error_envelope(
                    "Approval request not found or expired",
                    status_code=404,
                ),
                status=404,
            )

        # Clean up the approval request
        cache.delete(f"{APPROVE_PREFIX}{request_id}")

        redirect_uri = data.get("redirect_uri", "")
        state = data.get("state")

        if not approved:
            # Denied
            from mcp.server.auth.provider import construct_redirect_uri

            redirect_url = construct_redirect_uri(
                redirect_uri,
                error="access_denied",
                error_description="User denied the authorization request",
                state=state,
            )
            return Response(
                {
                    "status": True,
                    "result": {"redirect_url": redirect_url},
                }
            )

        # Approved - generate authorization code
        code_value = secrets.token_urlsafe(32)
        scopes = selected_groups if selected_groups else data.get("scopes", [])

        from pydantic import AnyUrl

        auth_code = FutureAGIAuthorizationCode(
            code=code_value,
            scopes=scopes,
            expires_at=time.time() + CODE_TTL,
            client_id=data.get("client_id", ""),
            code_challenge=data.get("code_challenge", ""),
            redirect_uri=AnyUrl(redirect_uri),
            redirect_uri_provided_explicitly=data.get(
                "redirect_uri_provided_explicitly", True
            ),
            resource=data.get("resource"),
            user_id=str(user.id),
            organization_id=str(organization.id),
            workspace_id=str(workspace.id) if workspace else None,
        )

        cache.set(
            f"{CODE_PREFIX}{code_value}",
            auth_code.model_dump(mode="json"),
            CODE_TTL,
        )

        from mcp.server.auth.provider import construct_redirect_uri

        redirect_url = construct_redirect_uri(
            redirect_uri,
            code=code_value,
            state=state,
        )

        logger.info(
            "oauth_user_approved",
            user_id=str(user.id),
            client_id=data.get("client_id"),
            scopes=scopes,
        )

        return Response(
            {
                "status": True,
                "result": {"redirect_url": redirect_url},
            }
        )

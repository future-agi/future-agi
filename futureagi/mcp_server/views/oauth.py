"""OAuth 2.0 authorization code flow endpoints for MCP Server."""

from urllib.parse import urlencode

import structlog
from django.db import DatabaseError
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from mcp_server.constants import TOOL_GROUPS
from mcp_server.models.connection import MCPConnection
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.oauth_utils import (
    decrypt_oauth_token,
    generate_authorization_code,
    generate_oauth_token,
    generate_refresh_token,
    verify_client_secret,
)
from mcp_server.serializers.contracts import (
    MCPErrorResponseSerializer,
    MCPOAuthAuthorizeResponseSerializer,
    MCPOAuthConsentRequestSerializer,
    MCPOAuthRedirectResponseSerializer,
    MCPOAuthTokenErrorResponseSerializer,
    MCPOAuthTokenRequestSerializer,
    MCPOAuthTokenResponseSerializer,
)
from tfc.utils.api_contracts import validated_request

logger = structlog.get_logger(__name__)


def _client_registry_unavailable_response(protocol="management"):
    if protocol == "oauth":
        return Response(
            {
                "error": "server_error",
                "error_description": "OAuth client registry unavailable",
            },
            status=503,
        )
    return Response(
        {"status": False, "error": "OAuth client registry unavailable"},
        status=503,
    )


def _get_active_oauth_client(client_id, protocol="management"):
    try:
        return MCPOAuthClient.objects.get(client_id=client_id, is_active=True), None
    except MCPOAuthClient.DoesNotExist:
        return None, None
    except DatabaseError:
        logger.exception("mcp_oauth_client_registry_unavailable", client_id=client_id)
        return None, _client_registry_unavailable_response(protocol=protocol)


def _oauth_validation_error_response(errors):
    grant_type_errors = errors.get("grant_type") if isinstance(errors, dict) else None
    error_code = "invalid_request"
    if grant_type_errors and any(
        "valid choice" in str(error) for error in grant_type_errors
    ):
        error_code = "unsupported_grant_type"

    def _field_errors_to_text(field_errors):
        messages = (
            field_errors if isinstance(field_errors, (list, tuple)) else [field_errors]
        )
        return ", ".join(str(error) for error in messages)

    return Response(
        {
            "error": error_code,
            "error_description": "; ".join(
                f"{field}: {_field_errors_to_text(field_errors)}"
                for field, field_errors in errors.items()
            )
            if isinstance(errors, dict)
            else str(errors),
        },
        status=400,
    )


class MCPOAuthAuthorizeView(APIView):
    """GET /mcp/oauth/authorize/ — Return consent screen data."""

    @swagger_auto_schema(
        responses={
            200: MCPOAuthAuthorizeResponseSerializer,
            400: MCPErrorResponseSerializer,
        },
    )
    def get(self, request):
        client_id = request.query_params.get("client_id")
        redirect_uri = request.query_params.get("redirect_uri")
        response_type = request.query_params.get("response_type")
        scope = request.query_params.get("scope", "")
        state = request.query_params.get("state", "")

        if not client_id or not redirect_uri:
            return Response(
                {"status": False, "error": "Missing client_id or redirect_uri"},
                status=400,
            )

        if response_type != "code":
            return Response(
                {"status": False, "error": "Unsupported response_type, must be 'code'"},
                status=400,
            )

        client, error_response = _get_active_oauth_client(client_id)
        if error_response is not None:
            return error_response
        if client is None:
            return Response(
                {"status": False, "error": "Unknown client_id"},
                status=400,
            )

        if redirect_uri not in client.redirect_uris:
            return Response(
                {"status": False, "error": "Invalid redirect_uri"},
                status=400,
            )

        # Parse requested scopes (comma-separated tool group slugs)
        requested_groups = (
            [s.strip() for s in scope.split(",") if s.strip()] if scope else []
        )

        # Build available tool groups with checked status
        available_groups = []
        for slug, meta in TOOL_GROUPS.items():
            available_groups.append(
                {
                    "slug": slug,
                    "name": meta["name"],
                    "description": meta["description"],
                    "checked": slug in requested_groups if requested_groups else True,
                }
            )

        return Response(
            {
                "status": True,
                "result": {
                    "client_name": client.name,
                    "client_id": client.client_id,
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "available_groups": available_groups,
                },
            }
        )


class MCPOAuthConsentView(APIView):
    """POST /mcp/oauth/consent/ — Process user consent decision."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=MCPOAuthConsentRequestSerializer,
        responses={
            200: MCPOAuthRedirectResponseSerializer,
            400: MCPErrorResponseSerializer,
            403: MCPErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        user = request.user
        organization = getattr(request, "organization", None) or getattr(
            user, "organization", None
        )
        workspace = getattr(request, "workspace", None)

        client_id = request.validated_data["client_id"]
        redirect_uri = request.validated_data["redirect_uri"]
        state = request.validated_data.get("state", "")
        approved = request.validated_data.get("approved", False)
        selected_groups = request.validated_data.get("selected_groups", [])

        if not organization:
            return Response(
                {"status": False, "error": "No organization context"},
                status=403,
            )

        client, error_response = _get_active_oauth_client(client_id)
        if error_response is not None:
            return error_response
        if client is None:
            return Response(
                {"status": False, "error": "Unknown client_id"},
                status=400,
            )

        if redirect_uri not in client.redirect_uris:
            return Response(
                {"status": False, "error": "Invalid redirect_uri"},
                status=400,
            )

        # Denied
        if not approved:
            params = urlencode({"error": "access_denied", "state": state})
            return Response(
                {
                    "status": True,
                    "result": {"redirect_url": f"{redirect_uri}?{params}"},
                }
            )

        # Approved — generate authorization code
        code_value = generate_authorization_code()
        MCPOAuthCode.objects.create(
            code=code_value,
            client=client,
            user=user,
            organization=organization,
            workspace=workspace,
            redirect_uri=redirect_uri,
            scope=selected_groups,
            state=state,
        )

        params = urlencode({"code": code_value, "state": state})
        logger.info(
            "oauth_code_issued",
            client_id=client_id,
            user_id=str(user.id),
            scope=selected_groups,
        )

        return Response(
            {
                "status": True,
                "result": {"redirect_url": f"{redirect_uri}?{params}"},
            }
        )


class MCPOAuthTokenView(APIView):
    """POST /mcp/oauth/token/ — Exchange code or refresh token for access token."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @validated_request(
        request_serializer=MCPOAuthTokenRequestSerializer,
        responses={
            200: MCPOAuthTokenResponseSerializer,
            400: MCPOAuthTokenErrorResponseSerializer,
            401: MCPOAuthTokenErrorResponseSerializer,
        },
        validation_error_response=_oauth_validation_error_response,
    )
    def post(self, request):
        data = request.validated_data
        grant_type = data.get("grant_type")

        if grant_type == "authorization_code":
            return self._handle_authorization_code(data)
        elif grant_type == "refresh_token":
            return self._handle_refresh_token(data)
        else:
            return Response(
                {"error": "unsupported_grant_type"},
                status=400,
            )

    def _handle_authorization_code(self, data):
        code = data.get("code")
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
        redirect_uri = data.get("redirect_uri")

        if not all([code, client_id, client_secret]):
            return Response({"error": "invalid_request"}, status=400)

        # Validate client
        client, error_response = _get_active_oauth_client(client_id, protocol="oauth")
        if error_response is not None:
            return error_response
        if client is None:
            return Response({"error": "invalid_client"}, status=401)

        if not verify_client_secret(client_secret, client.client_secret_hash):
            return Response({"error": "invalid_client"}, status=401)

        # Validate authorization code
        try:
            auth_code = MCPOAuthCode.objects.select_related(
                "user", "organization", "workspace"
            ).get(code=code, client=client, used=False)
        except MCPOAuthCode.DoesNotExist:
            return Response({"error": "invalid_grant"}, status=400)

        if auth_code.is_expired:
            return Response(
                {"error": "invalid_grant", "error_description": "Code expired"},
                status=400,
            )

        if redirect_uri and auth_code.redirect_uri != redirect_uri:
            return Response(
                {
                    "error": "invalid_grant",
                    "error_description": "Redirect URI mismatch",
                },
                status=400,
            )

        # Mark code as used
        auth_code.used = True
        auth_code.save(update_fields=["used"])

        # Generate tokens
        access_token, expires_at = generate_oauth_token(
            user_id=auth_code.user.id,
            org_id=auth_code.organization.id,
            workspace_id=auth_code.workspace.id if auth_code.workspace else None,
            client_id=client_id,
            scope=auth_code.scope,
        )
        refresh_token = generate_refresh_token(
            user_id=auth_code.user.id,
            org_id=auth_code.organization.id,
            client_id=client_id,
        )

        # Store tokens on MCPConnection
        connection, created = MCPConnection.no_workspace_objects.get_or_create(
            user=auth_code.user,
            workspace=auth_code.workspace,
            deleted=False,
            defaults={
                "organization": auth_code.organization,
                "is_active": True,
            },
        )
        connection.oauth_token_encrypted = access_token
        connection.oauth_refresh_token_encrypted = refresh_token
        connection.oauth_token_expires_at = expires_at
        connection.save(
            update_fields=[
                "oauth_token_encrypted",
                "oauth_refresh_token_encrypted",
                "oauth_token_expires_at",
            ]
        )

        # Set enabled tool groups from consent scope
        config, _ = MCPToolGroupConfig.no_workspace_objects.get_or_create(
            connection=connection,
        )
        config.enabled_groups = auth_code.scope
        config.save(update_fields=["enabled_groups"])

        logger.info(
            "oauth_token_issued",
            client_id=client_id,
            user_id=str(auth_code.user.id),
            grant_type="authorization_code",
        )

        return Response(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh_token,
                "scope": ",".join(auth_code.scope),
            }
        )

    def _handle_refresh_token(self, data):
        refresh_token = data.get("refresh_token")
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")

        if not all([refresh_token, client_id, client_secret]):
            return Response({"error": "invalid_request"}, status=400)

        # Validate client
        client, error_response = _get_active_oauth_client(client_id, protocol="oauth")
        if error_response is not None:
            return error_response
        if client is None:
            return Response({"error": "invalid_client"}, status=401)

        if not verify_client_secret(client_secret, client.client_secret_hash):
            return Response({"error": "invalid_client"}, status=401)

        # Decrypt and validate refresh token
        payload = decrypt_oauth_token(refresh_token)
        if not payload or payload.get("type") != "mcp_refresh":
            return Response({"error": "invalid_grant"}, status=400)

        if payload.get("client_id") != client_id:
            return Response({"error": "invalid_grant"}, status=400)

        user_id = payload["user_id"]
        org_id = payload["org_id"]

        # Find the connection to get workspace and scope
        try:
            connection = MCPConnection.no_workspace_objects.get(
                user_id=user_id,
                oauth_refresh_token_encrypted=refresh_token,
                deleted=False,
            )
        except MCPConnection.DoesNotExist:
            return Response({"error": "invalid_grant"}, status=400)

        # Get current scope from tool config
        try:
            config = connection.tool_config
            scope = config.enabled_groups
        except MCPToolGroupConfig.DoesNotExist:
            scope = []

        # Generate new access token
        access_token, expires_at = generate_oauth_token(
            user_id=user_id,
            org_id=org_id,
            workspace_id=(
                str(connection.workspace_id) if connection.workspace_id else None
            ),
            client_id=client_id,
            scope=scope,
        )

        # Update stored token
        connection.oauth_token_encrypted = access_token
        connection.oauth_token_expires_at = expires_at
        connection.save(
            update_fields=["oauth_token_encrypted", "oauth_token_expires_at"]
        )

        logger.info(
            "oauth_token_refreshed",
            client_id=client_id,
            user_id=user_id,
            grant_type="refresh_token",
        )

        return Response(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": ",".join(scope),
            }
        )

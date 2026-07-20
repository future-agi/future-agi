"""OAuth 2.0 authorization code flow endpoints for MCP Server."""

from urllib.parse import urlencode

import structlog
from django.conf import settings
from rest_framework.permissions import AllowAny
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
from mcp_server.throttles import (
    MCPOAuthTokenClientThrottle,
    MCPOAuthTokenIPThrottle,
    get_invalid_attempt_lockout_wait,
    record_invalid_token_attempt,
    request_data_value,
    stable_hash,
)

logger = structlog.get_logger(__name__)


class MCPOAuthAuthorizeView(APIView):
    """GET /mcp/oauth/authorize/ — Return consent screen data."""

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

        try:
            client = MCPOAuthClient.objects.get(client_id=client_id, is_active=True)
        except MCPOAuthClient.DoesNotExist:
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

    def post(self, request):
        user = request.user
        organization = getattr(request, "organization", None) or getattr(
            user, "organization", None
        )
        workspace = getattr(request, "workspace", None)

        client_id = request.data.get("client_id")
        redirect_uri = request.data.get("redirect_uri")
        state = request.data.get("state", "")
        approved = request.data.get("approved", False)
        selected_groups = request.data.get("selected_groups", [])

        if not client_id or not redirect_uri:
            return Response(
                {"status": False, "error": "Missing client_id or redirect_uri"},
                status=400,
            )

        if not organization:
            return Response(
                {"status": False, "error": "No organization context"},
                status=403,
            )

        try:
            client = MCPOAuthClient.objects.get(client_id=client_id, is_active=True)
        except MCPOAuthClient.DoesNotExist:
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
    """POST /mcp/oauth/token/ - Exchange code or refresh token for access token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MCPOAuthTokenIPThrottle, MCPOAuthTokenClientThrottle]

    def _token_log_context(self, request):
        client_id = request_data_value(request, "client_id")
        remote_addr = request.META.get("REMOTE_ADDR", "")
        return {
            "grant_type": request_data_value(request, "grant_type") or "unknown",
            "client_id_hash": (
                stable_hash(client_id, length=16) if client_id else None
            ),
            "remote_addr_hash": (
                stable_hash(remote_addr, length=16) if remote_addr else None
            ),
        }

    def _log_token_warning(self, request, event_name, **kwargs):
        logger.warning(event_name, **self._token_log_context(request), **kwargs)

    def throttled(self, request, wait):
        self._log_token_warning(
            request,
            "mcp_oauth_token_throttled",
            retry_after_seconds=wait,
        )
        return super().throttled(request, wait)

    def _invalid_token_response(
        self,
        request,
        *,
        error,
        status,
        reason,
        error_description=None,
    ):
        lockout_seconds = record_invalid_token_attempt(request)
        self._log_token_warning(
            request,
            "mcp_oauth_token_invalid_attempt",
            reason=reason,
            lockout_seconds=lockout_seconds or None,
        )
        body = {"error": error}
        if error_description:
            body["error_description"] = error_description
        return Response(body, status=status)

    def post(self, request):
        lockout_wait = get_invalid_attempt_lockout_wait(request)
        if lockout_wait:
            self._log_token_warning(
                request,
                "mcp_oauth_token_invalid_attempt_lockout",
                retry_after_seconds=lockout_wait,
            )
            return Response(
                {
                    "error": "slow_down",
                    "error_description": "Too many invalid token attempts",
                },
                status=429,
                headers={"Retry-After": str(lockout_wait)},
            )

        grant_type = request.data.get("grant_type")

        if grant_type == "authorization_code":
            return self._handle_authorization_code(request)
        elif grant_type == "refresh_token":
            return self._handle_refresh_token(request)
        else:
            return Response(
                {"error": "unsupported_grant_type"},
                status=400,
            )

    def _handle_authorization_code(self, request):
        code = request.data.get("code")
        client_id = request.data.get("client_id")
        client_secret = request.data.get("client_secret")
        redirect_uri = request.data.get("redirect_uri")

        if not all([code, client_id, client_secret]):
            return Response({"error": "invalid_request"}, status=400)

        # Validate client
        try:
            client = MCPOAuthClient.objects.get(client_id=client_id, is_active=True)
        except MCPOAuthClient.DoesNotExist:
            return self._invalid_token_response(
                request,
                error="invalid_client",
                status=401,
                reason="unknown_client",
            )

        if not verify_client_secret(client_secret, client.client_secret_hash):
            return self._invalid_token_response(
                request,
                error="invalid_client",
                status=401,
                reason="invalid_client_secret",
            )

        # Validate authorization code
        try:
            auth_code = MCPOAuthCode.objects.select_related(
                "user", "organization", "workspace"
            ).get(code=code, client=client, used=False)
        except MCPOAuthCode.DoesNotExist:
            return self._invalid_token_response(
                request,
                error="invalid_grant",
                status=400,
                reason="authorization_code_not_found",
            )

        if auth_code.is_expired:
            return self._invalid_token_response(
                request,
                error="invalid_grant",
                status=400,
                reason="authorization_code_expired",
                error_description="Code expired",
            )

        if redirect_uri and auth_code.redirect_uri != redirect_uri:
            return self._invalid_token_response(
                request,
                error="invalid_grant",
                status=400,
                reason="redirect_uri_mismatch",
                error_description="Redirect URI mismatch",
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

    def _handle_refresh_token(self, request):
        refresh_token = request.data.get("refresh_token")
        client_id = request.data.get("client_id")
        client_secret = request.data.get("client_secret")

        if not all([refresh_token, client_id, client_secret]):
            return Response({"error": "invalid_request"}, status=400)

        # Validate client
        try:
            client = MCPOAuthClient.objects.get(client_id=client_id, is_active=True)
        except MCPOAuthClient.DoesNotExist:
            return self._invalid_token_response(
                request,
                error="invalid_client",
                status=401,
                reason="unknown_client",
            )

        if not verify_client_secret(client_secret, client.client_secret_hash):
            return self._invalid_token_response(
                request,
                error="invalid_client",
                status=401,
                reason="invalid_client_secret",
            )

        # Decrypt and validate refresh token
        payload = decrypt_oauth_token(refresh_token)
        if not payload or payload.get("type") != "mcp_refresh":
            return self._invalid_token_response(
                request,
                error="invalid_grant",
                status=400,
                reason="invalid_refresh_token",
            )

        if payload.get("client_id") != client_id:
            return self._invalid_token_response(
                request,
                error="invalid_grant",
                status=400,
                reason="refresh_token_client_mismatch",
            )

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
            return self._invalid_token_response(
                request,
                error="invalid_grant",
                status=400,
                reason="refresh_connection_not_found",
            )

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

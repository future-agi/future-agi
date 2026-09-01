import structlog
from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from agentcc.services.credential_manager import encrypt_token
from ee.falcon_ai.mcp_proxy import MCPConnectorProxy
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.serializers_connectors import (
    MCPConnectorCreateSerializer,
    MCPConnectorDetailSerializer,
    MCPConnectorListSerializer,
    MCPConnectorToolsSerializer,
)
from ee.falcon_ai.serializers_contracts import (
    FalconEmptyRequestSerializer,
    FalconErrorResponseSerializer,
    MCPConnectorAuthenticateResponseSerializer,
    MCPConnectorDetailResponseSerializer,
    MCPConnectorDiscoverResponseSerializer,
    MCPConnectorListResponseSerializer,
    MCPConnectorTestResponseSerializer,
    MCPConnectorUpdateRequestSerializer,
    MCPOAuthCallbackQuerySerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()


def _get_connector_for_request(request, connector_id):
    organization = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    if not organization:
        return None, _gm.forbidden_response("No organization context")

    filters = {
        "id": connector_id,
        "organization": organization,
    }
    if workspace:
        filters["workspace"] = workspace

    try:
        return MCPConnector.objects.get(**filters), None
    except MCPConnector.DoesNotExist:
        return None, _gm.not_found("Connector not found")


class MCPConnectorListView(APIView):
    """List and create MCP connectors."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MCPConnectorListResponseSerializer,
            403: FalconErrorResponseSerializer,
        }
    )
    def get(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return _gm.forbidden_response("No organization context")

        connectors = MCPConnector.objects.filter(
            organization=organization,
        )

        if workspace:
            connectors = connectors.filter(workspace=workspace)

        serializer = MCPConnectorListSerializer(connectors, many=True)
        return Response({"status": True, "results": serializer.data})

    @swagger_auto_schema(
        request_body=MCPConnectorCreateSerializer,
        responses={
            201: MCPConnectorDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
            409: FalconErrorResponseSerializer,
        },
    )
    def post(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization or not workspace:
            return _gm.forbidden_response("Organization and workspace context required")

        serializer = MCPConnectorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Check for name uniqueness within workspace
        if MCPConnector.objects.filter(workspace=workspace, name=data["name"]).exists():
            return _gm.custom_error_response(
                status.HTTP_409_CONFLICT,
                f"Connector with name '{data['name']}' already exists",
            )

        connector = MCPConnector.objects.create(
            organization=organization,
            workspace=workspace,
            name=data["name"],
            server_url=data["server_url"],
            transport=data["transport"],
            auth_type=data["auth_type"],
            auth_header_name=data.get("auth_header_name", "Authorization"),
            auth_header_value=encrypt_token(data.get("auth_header_value", "")),
            created_by=request.user,
        )

        result_serializer = MCPConnectorDetailSerializer(connector)
        return Response(
            {"status": True, "result": result_serializer.data},
            status=status.HTTP_201_CREATED,
        )


class MCPConnectorDetailView(APIView):
    """Get, update, or delete an MCP connector."""

    permission_classes = [IsAuthenticated]

    def _get_connector(self, request, connector_id):
        return _get_connector_for_request(request, connector_id)

    @swagger_auto_schema(
        responses={
            200: MCPConnectorDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        }
    )
    def get(self, request, connector_id):
        connector, error = self._get_connector(request, connector_id)
        if error:
            return error

        serializer = MCPConnectorDetailSerializer(connector)
        return Response({"status": True, "result": serializer.data})

    @validated_request(
        MCPConnectorUpdateRequestSerializer,
        responses={
            200: MCPConnectorDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        },
    )
    def patch(self, request, connector_id):
        connector, error = self._get_connector(request, connector_id)
        if error:
            return error

        updatable_fields = [
            "name",
            "server_url",
            "transport",
            "auth_type",
            "auth_header_name",
            "auth_header_value",
            "is_active",
        ]
        update_fields = []
        for field in updatable_fields:
            if field in request.validated_data:
                value = request.validated_data[field]
                if field == "auth_header_value":
                    value = encrypt_token(value)
                setattr(connector, field, value)
                update_fields.append(field)

        if update_fields:
            update_fields.append("updated_at")
            connector.save(update_fields=update_fields)

        serializer = MCPConnectorDetailSerializer(connector)
        return Response({"status": True, "result": serializer.data})

    @swagger_auto_schema(
        responses={
            204: "Connector deleted",
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        }
    )
    def delete(self, request, connector_id):
        connector, error = self._get_connector(request, connector_id)
        if error:
            return error

        # Soft delete via BaseModel.delete()
        connector.delete()
        return Response({"status": True}, status=status.HTTP_204_NO_CONTENT)


class MCPConnectorDiscoverView(APIView):
    """Discover tools from an external MCP server."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=FalconEmptyRequestSerializer,
        responses={
            200: MCPConnectorDiscoverResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
        },
    )
    def post(self, request, connector_id):
        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        proxy = MCPConnectorProxy()
        result = proxy.discover_tools_sync(connector)

        if result["success"]:
            connector.refresh_from_db()
            serializer = MCPConnectorDetailSerializer(connector)
            return Response(
                {
                    "status": True,
                    "result": serializer.data,
                    "discovered_count": result["count"],
                }
            )
        else:
            return _gm.custom_error_response(
                status.HTTP_502_BAD_GATEWAY,
                result["error"],
            )


class MCPConnectorTestView(APIView):
    """Test connection to an external MCP server."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=FalconEmptyRequestSerializer,
        responses={
            200: MCPConnectorTestResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
        },
    )
    def post(self, request, connector_id):
        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        proxy = MCPConnectorProxy()
        result = proxy.test_connection_sync(connector)

        if result["success"]:
            return Response({"status": True, "result": result})
        else:
            return _gm.custom_error_response(
                status.HTTP_502_BAD_GATEWAY,
                result.get("error", "Connection failed"),
            )


class MCPConnectorAuthenticateView(APIView):
    """Initiate or re-initiate authentication for a connector.

    For OAuth connectors, returns the authorization URL to open in a browser.
    For API key connectors, tests the connection and returns status.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=FalconEmptyRequestSerializer,
        responses={
            200: MCPConnectorAuthenticateResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
        },
    )
    def post(self, request, connector_id):
        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        # If auth_type is not set or "none", try to auto-detect by connecting
        if connector.auth_type in ("none", ""):
            import httpx

            try:
                resp = httpx.get(
                    connector.server_url,
                    timeout=10,
                    follow_redirects=True,
                    headers={"Accept": "text/event-stream"},
                )
                if resp.status_code == 401:
                    # Server requires auth — check if it supports OAuth
                    connector.auth_type = "oauth"
                    connector.save(update_fields=["auth_type", "updated_at"])
                    logger.info(
                        "oauth_auto_detected",
                        connector=connector.name,
                        url=connector.server_url,
                    )
            except Exception:
                pass  # Will fall through to test below

        # OAuth flow: discover metadata, build auth URL, return it
        if connector.auth_type == "oauth":
            from ee.falcon_ai.oauth_client import build_authorization_url

            redirect_uri = request.build_absolute_uri(
                f"/falcon-ai/mcp-connectors/{connector.id}/oauth/callback/"
            )

            try:
                auth_url = build_authorization_url(connector, redirect_uri)
                return Response(
                    {
                        "status": True,
                        "auth_type": "oauth",
                        "authorization_url": auth_url,
                        "message": "Open the authorization URL to authenticate.",
                    }
                )
            except Exception as e:
                logger.error(
                    "oauth_auth_url_error",
                    connector=str(connector_id),
                    error=str(e),
                )
                return _gm.custom_error_response(
                    status.HTTP_502_BAD_GATEWAY,
                    f"OAuth setup failed: {e}",
                )

        # Non-OAuth: test the connection and discover tools
        proxy = MCPConnectorProxy()
        test_result = proxy.test_connection_sync(connector)

        if test_result.get("success"):
            connector.is_verified = True
            connector.last_error = ""
            connector.save(update_fields=["is_verified", "last_error", "updated_at"])

            # Also discover tools on successful auth
            discover_result = proxy.discover_tools_sync(connector)
            if discover_result.get("success"):
                connector.discovered_tools = discover_result.get("tools", [])
                connector.save(update_fields=["discovered_tools", "updated_at"])
        else:
            connector.is_verified = False
            connector.last_error = test_result.get("error", "Connection failed")
            connector.save(update_fields=["is_verified", "last_error", "updated_at"])

        serializer = MCPConnectorDetailSerializer(connector)
        return Response(
            {
                "status": test_result.get("success", False),
                "result": serializer.data,
                "message": (
                    "Connected successfully"
                    if test_result.get("success")
                    else test_result.get("error", "Connection failed")
                ),
            }
        )


class MCPConnectorToolsView(APIView):
    """Enable or disable specific tools on a connector."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=MCPConnectorToolsSerializer,
        responses={
            200: MCPConnectorDetailResponseSerializer,
            400: FalconErrorResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        },
    )
    def patch(self, request, connector_id):
        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        serializer = MCPConnectorToolsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Validate that the enabled tools are a subset of discovered tools
        discovered_names = {
            t.get("name", "") for t in (connector.discovered_tools or [])
        }
        requested_names = set(serializer.validated_data["enabled_tool_names"])
        invalid_names = requested_names - discovered_names

        if invalid_names:
            return _gm.bad_request(
                f"Unknown tools: {', '.join(sorted(invalid_names))}"
            )

        connector.enabled_tool_names = list(requested_names)
        connector.save(update_fields=["enabled_tool_names", "updated_at"])

        result_serializer = MCPConnectorDetailSerializer(connector)
        return Response({"status": True, "result": result_serializer.data})


class MCPConnectorOAuthCallbackView(APIView):
    """Handle the OAuth 2.1 redirect callback from an authorization server.

    This is a GET endpoint that the browser is redirected to after the user
    approves the OAuth consent. It exchanges the authorization code for
    tokens, then returns an HTML page that posts a message to the opener
    window and closes itself.

    Uses AllowAny because this is a browser redirect with state validation.
    """

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        query_serializer=MCPOAuthCallbackQuerySerializer,
        responses={
            200: openapi.Response(
                description="HTML page that posts OAuth result to opener.",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
            400: openapi.Response(
                description="HTML page that posts OAuth failure to opener.",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
            502: openapi.Response(
                description="HTML page that posts OAuth token exchange failure to opener.",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
        },
    )
    def get(self, request, connector_id):
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")
        error_description = request.query_params.get(
            "error_description", "Authorization denied"
        )

        # Handle error from authorization server
        if error:
            logger.warning(
                "oauth_callback_error",
                connector=str(connector_id),
                error=error,
                description=error_description,
            )
            return self._close_window_response(
                success=False,
                message=error_description,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if not code or not state:
            return self._close_window_response(
                success=False,
                message="Missing code or state parameter",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Look up connector and validate state
        try:
            connector = MCPConnector.objects.get(
                id=connector_id,
                oauth_state=state,
            )
        except MCPConnector.DoesNotExist:
            logger.warning(
                "oauth_callback_invalid_state",
                connector=str(connector_id),
                state=state,
            )
            return self._close_window_response(
                success=False,
                message="Invalid state parameter; possible CSRF attack",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Exchange code for tokens
        from ee.falcon_ai.oauth_client import exchange_code_for_tokens

        redirect_uri = request.build_absolute_uri(
            f"/falcon-ai/mcp-connectors/{connector.id}/oauth/callback/"
        )

        try:
            exchange_code_for_tokens(connector, code, redirect_uri)
        except Exception as e:
            logger.error(
                "oauth_token_exchange_error",
                connector=connector.name,
                error=str(e),
            )
            connector.last_error = str(e)[:500]
            connector.save(update_fields=["last_error", "updated_at"])
            return self._close_window_response(
                success=False,
                message=f"Token exchange failed: {e}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        # Token exchange succeeded — discover tools (best effort).
        # If discovery fails, connector is still verified with valid tokens;
        # the user can manually trigger discovery from the UI.
        try:
            proxy = MCPConnectorProxy()
            result = proxy.discover_tools_sync(connector)
            if result.get("success"):
                logger.info(
                    "oauth_callback_tools_discovered",
                    connector=connector.name,
                    count=result.get("count", 0),
                )
            else:
                logger.warning(
                    "oauth_callback_discovery_failed",
                    connector=connector.name,
                    error=result.get("error"),
                )
                # Keep is_verified=True since tokens are valid
                connector.is_verified = True
                connector.last_error = ""
                connector.save(
                    update_fields=["is_verified", "last_error", "updated_at"]
                )
        except Exception as e:
            logger.warning(
                "oauth_callback_discovery_error",
                connector=connector.name,
                error=str(e),
            )
            # Keep is_verified=True since tokens are valid
            connector.is_verified = True
            connector.last_error = ""
            connector.save(update_fields=["is_verified", "last_error", "updated_at"])

        logger.info("oauth_callback_success", connector=connector.name)
        return self._close_window_response(
            success=True, message="Connected successfully"
        )

    def _close_window_response(
        self,
        success: bool,
        message: str,
        status_code: int = status.HTTP_200_OK,
    ) -> HttpResponse:
        """Return an HTML page that posts a message to the opener and closes."""
        status_str = "success" if success else "error"
        # Escape for safe embedding in JS string
        safe_message = (
            message.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace('"', '\\"')
            .replace("\n", "\\n")
        )
        html = f"""<!DOCTYPE html>
<html>
<head><title>OAuth Callback</title></head>
<body>
<p>{message}</p>
<script>
(function() {{
    var msg = {{
        type: 'falcon_oauth_callback',
        status: '{status_str}',
        message: '{safe_message}'
    }};
    if (window.opener) {{
        window.opener.postMessage(msg, '*');
    }}
    setTimeout(function() {{ window.close(); }}, 1500);
}})();
</script>
</body>
</html>"""
        return HttpResponse(html, content_type="text/html", status=status_code)

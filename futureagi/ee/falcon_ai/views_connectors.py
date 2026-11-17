import structlog
from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ee.falcon_ai.credential_responses import (
    FalconCredentialAPIView,
    credential_auth_required_response,
)
from ee.falcon_ai.credentials import (
    AUTH_REQUIRED,
    FalconCredentialError,
    encrypt_falcon_token,
)
from ee.falcon_ai.mcp_proxy import (
    NOT_A_CONNECTOR,
    MCPConnectorProxy,
    check_connector_call_address,
)
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.oauth_registration import create_connector, save_connector
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
from tfc.permissions.rbac import IsOrganizationAdmin
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.ssrf_guard import (
    check_endpoint_url,
    guarded_httpx_client,
    is_stripped_endpoint_header,
)

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()

SERVER_ADDRESS_REFUSED = "This server address can't be used for a connector."
HEADER_NAME_REFUSED = (
    "This header name can't be used for a connector's credential. "
    "Choose another header name."
)
ADD_NEEDS_OWNER_OR_ADMIN = (
    "Only an organization owner or admin can add a connector. "
    "Ask one of them to add it."
)
CHANGE_NEEDS_OWNER_OR_ADMIN = (
    "Only an organization owner or admin can change a connector. "
    "Ask one of them to change it."
)


def _server_address_refusal(url):
    """None when a connector may be saved at ``url``; otherwise the answer.

    The address is one this deployment calls (https:// on Future AGI Cloud, a
    public name where only public addresses are allowed), and every address
    its host resolves to is allowed. The answer is one sentence for every
    reason and names neither the address nor the host.
    """
    try:
        check_connector_call_address(url)
        check_endpoint_url(url)
    except ValueError as exc:
        logger.warning("connector_address_refused", error_class=type(exc).__name__)
        return _gm.bad_request(SERVER_ADDRESS_REFUSED)
    return None


def _cleared_sign_in():
    """The fields disconnecting a connector clears: its credential, its
    sign-in and the sign-in addresses its server advertised."""
    from ee.falcon_ai.disconnect import CREDENTIAL_FIELDS

    return {
        **dict.fromkeys(CREDENTIAL_FIELDS, ""),
        "oauth_client_id": "",
        "oauth_server_metadata": {},
        "oauth_token_expires_at": None,
        "is_verified": False,
        "last_error": "",
    }


CLIENT_SECRET_NEEDS_CLIENT_ID = "Enter the OAuth client ID for this secret."

# What a new OAuth client ID replaces: the secret and the sign-in the old
# client issued. The sign-in addresses its server advertised stay.
_CLIENT_REPLACED_CLEARS = {
    "oauth_client_secret": "",
    "oauth_access_token": "",
    "oauth_refresh_token": "",
    "oauth_code_verifier": "",
    "oauth_state": "",
    "oauth_token_expires_at": None,
    "is_verified": False,
    "last_error": "",
}


def _oauth_client_changes(data, stored_client_id):
    """The stored changes for the OAuth client fields of a change request, and
    the sentence refusing it (or None).

    A blank or missing secret keeps the saved one and ``null`` removes it. A
    different client ID drops the old client's secret and sign-in. A secret
    needs a client ID to belong to.
    """
    changes = {}
    if "oauth_client_id" in data and data["oauth_client_id"] != stored_client_id:
        changes = {
            **_CLIENT_REPLACED_CLEARS,
            "oauth_client_id": data["oauth_client_id"],
        }
    secret = data.get("oauth_client_secret", "")
    if secret is None:
        changes["oauth_client_secret"] = ""
    elif secret:
        if not changes.get("oauth_client_id", stored_client_id):
            return {}, CLIENT_SECRET_NEEDS_CLIENT_ID
        changes["oauth_client_secret"] = encrypt_falcon_token(secret)
    return changes, None


def _owner_or_admin_refusal(request, view, sentence):
    """None when the person is an owner or admin of the request's organization;
    otherwise the answer, which says who can.

    Adding, changing, removing, discovering, testing and signing a connector in
    are for an organization owner or admin. Each of them asks here first,
    before the connector is read and before any call is made.
    """
    if IsOrganizationAdmin().has_permission(request, view):
        return None
    return _gm.forbidden_response(sentence)


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


class MCPConnectorListView(FalconCredentialAPIView):
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
        refusal = _owner_or_admin_refusal(request, self, ADD_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

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

        # A credential header name the address check drops is refused.
        if is_stripped_endpoint_header(data.get("auth_header_name", "Authorization")):
            return _gm.bad_request(HEADER_NAME_REFUSED)

        refusal = _server_address_refusal(data["server_url"])
        if refusal:
            return refusal

        connector = create_connector(
            organization=organization,
            workspace=workspace,
            name=data["name"],
            server_url=data["server_url"],
            transport=data["transport"],
            auth_type=data["auth_type"],
            auth_header_name=data.get("auth_header_name", "Authorization"),
            auth_header_value=encrypt_falcon_token(data.get("auth_header_value", "")),
            oauth_client_id=data.get("oauth_client_id", ""),
            oauth_client_secret=encrypt_falcon_token(
                data.get("oauth_client_secret") or ""
            ),
            created_by=request.user,
        )

        result_serializer = MCPConnectorDetailSerializer(connector)
        return Response(
            {"status": True, "result": result_serializer.data},
            status=status.HTTP_201_CREATED,
        )


class MCPConnectorDetailView(FalconCredentialAPIView):
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
            409: FalconErrorResponseSerializer,
        },
    )
    def patch(self, request, connector_id):
        refusal = _owner_or_admin_refusal(request, self, CHANGE_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

        connector, error = self._get_connector(request, connector_id)
        if error:
            return error

        # A new credential header name the address check drops is refused.
        header_name = request.validated_data.get("auth_header_name")
        if (
            header_name is not None
            and header_name != connector.auth_header_name
            and is_stripped_endpoint_header(header_name)
        ):
            return _gm.bad_request(HEADER_NAME_REFUSED)

        new_url = request.validated_data.get("server_url")
        moved = new_url is not None and new_url != connector.server_url
        if moved:
            refusal = _server_address_refusal(new_url)
            if refusal:
                return refusal

        updatable_fields = [
            "name",
            "server_url",
            "transport",
            "auth_type",
            "auth_header_name",
            "auth_header_value",
            "is_active",
        ]
        changes = {}
        for field in updatable_fields:
            if field in request.validated_data:
                value = request.validated_data[field]
                if field == "auth_header_value":
                    value = encrypt_falcon_token(value)
                changes[field] = value

        # A connector moved to another address has no saved client to compare.
        client_changes, client_refusal = _oauth_client_changes(
            request.validated_data, "" if moved else connector.oauth_client_id
        )
        if client_refusal:
            return _gm.bad_request(client_refusal)
        changes = {**changes, **client_changes}

        # A connector moved to another address keeps none of the old sign-in;
        # a credential sent with the move is the one kept.
        if moved:
            changes = {**_cleared_sign_in(), **changes}

        if changes:
            save_connector(connector, changes)

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
        refusal = _owner_or_admin_refusal(request, self, CHANGE_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

        connector, error = self._get_connector(request, connector_id)
        if error:
            return error

        from ee.falcon_ai.disconnect import disconnect_mcp

        disconnect_mcp(connector)
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
            409: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
        },
    )
    def post(self, request, connector_id):
        refusal = _owner_or_admin_refusal(request, self, CHANGE_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        proxy = MCPConnectorProxy()
        result = proxy.discover_tools_sync(connector)
        if result.get("code") == AUTH_REQUIRED:
            return credential_auth_required_response()

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
            409: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
        },
    )
    def post(self, request, connector_id):
        refusal = _owner_or_admin_refusal(request, self, CHANGE_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        proxy = MCPConnectorProxy()
        result = proxy.test_connection_sync(connector)
        if result.get("code") == AUTH_REQUIRED:
            return credential_auth_required_response()

        if result["success"]:
            return Response({"status": True, "result": result})
        else:
            if result.get("error") == NOT_A_CONNECTOR:
                proxy._save_error(connector, NOT_A_CONNECTOR)
            return _gm.custom_error_response(
                status.HTTP_502_BAD_GATEWAY,
                result.get("error", "Connection failed"),
            )


class MCPConnectorAuthenticateView(FalconCredentialAPIView):
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
            409: FalconErrorResponseSerializer,
            502: FalconErrorResponseSerializer,
        },
    )
    def post(self, request, connector_id):
        refusal = _owner_or_admin_refusal(request, self, CHANGE_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

        connector, error = _get_connector_for_request(request, connector_id)
        if error:
            return error

        # If auth_type is not set or "none", try to auto-detect by connecting
        if connector.auth_type in ("none", ""):
            try:
                # Only the status is read: the answer's body never is.
                check_connector_call_address(connector.server_url)
                with guarded_httpx_client(timeout=10) as client:
                    with client.stream(
                        "GET",
                        connector.server_url,
                        headers={"Accept": "text/event-stream"},
                    ) as resp:
                        status_code = resp.status_code
                if status_code == 401:
                    # Server requires auth — check if it supports OAuth
                    save_connector(connector, {"auth_type": "oauth"})
                    logger.info(
                        "oauth_auto_detected",
                        connector=connector.name,
                        url=connector.server_url,
                    )
            except FalconCredentialError:
                return credential_auth_required_response()
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
            except FalconCredentialError:
                return credential_auth_required_response()
            except Exception:
                logger.error(
                    "oauth_auth_url_error",
                    connector=str(connector_id),
                    error="OAuth request failed",
                )
                return _gm.custom_error_response(
                    status.HTTP_502_BAD_GATEWAY,
                    "OAuth setup failed",
                )

        # Non-OAuth: test the connection and discover tools
        proxy = MCPConnectorProxy()
        test_result = proxy.test_connection_sync(connector)
        if test_result.get("code") == AUTH_REQUIRED:
            return credential_auth_required_response()

        if test_result.get("success"):
            save_connector(connector, {"is_verified": True, "last_error": ""})

            # Also discover tools on successful auth
            discover_result = proxy.discover_tools_sync(connector)
            if discover_result.get("code") == AUTH_REQUIRED:
                return credential_auth_required_response()
            if discover_result.get("success"):
                save_connector(
                    connector, {"discovered_tools": discover_result.get("tools", [])}
                )
        else:
            last_error = (
                NOT_A_CONNECTOR
                if test_result.get("error") == NOT_A_CONNECTOR
                else "Connection failed"
            )
            save_connector(connector, {"is_verified": False, "last_error": last_error})

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


class MCPConnectorToolsView(FalconCredentialAPIView):
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
        refusal = _owner_or_admin_refusal(request, self, CHANGE_NEEDS_OWNER_OR_ADMIN)
        if refusal:
            return refusal

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
            return _gm.bad_request(f"Unknown tools: {', '.join(sorted(invalid_names))}")

        save_connector(connector, {"enabled_tool_names": list(requested_names)})

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
            409: openapi.Response(
                description="HTML page that posts CONNECTOR_AUTH_REQUIRED to opener.",
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

        # Handle error from authorization server
        if error:
            logger.warning(
                "oauth_callback_error",
                connector=str(connector_id),
            )
            return self._close_window_response(
                success=False,
                message="Authorization denied",
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
        except FalconCredentialError as error:
            # Registration mismatch is a static sentinel, not exception text.
            # A superseded callback must not even overwrite the current error.
            if error.args != (AUTH_REQUIRED,):
                MCPConnectorProxy()._save_error(connector, AUTH_REQUIRED)
            return self._close_window_response(
                success=False,
                message=AUTH_REQUIRED,
                status_code=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.error(
                "oauth_token_exchange_error",
                connector=connector.name,
                error="OAuth request failed",
            )
            save_connector(
                connector, {"last_error": "OAuth request failed"}, discard_stale=True
            )
            return self._close_window_response(
                success=False,
                message="Token exchange failed",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        # Token exchange succeeded — discover tools (best effort).
        # If discovery fails, connector is still verified with valid tokens;
        # the user can manually trigger discovery from the UI. A server whose
        # tool list is not a connector answer is not verified, and the page
        # says so.
        try:
            proxy = MCPConnectorProxy()
            result = proxy.discover_tools_sync(connector)
            if result.get("code") == AUTH_REQUIRED:
                return self._close_window_response(
                    success=False,
                    message=AUTH_REQUIRED,
                    status_code=status.HTTP_409_CONFLICT,
                )
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
                if result.get("error") == NOT_A_CONNECTOR:
                    # Discovery saved the connector as not verified, with the
                    # sentence as its last error, unless the row changed
                    # meanwhile; the sign-in's tokens stay.
                    return self._close_window_response(
                        success=False, message=NOT_A_CONNECTOR
                    )
                # Keep is_verified=True since tokens are valid
                save_connector(
                    connector,
                    {"is_verified": True, "last_error": ""},
                    discard_stale=True,
                )
        except Exception:
            logger.warning(
                "oauth_callback_discovery_error",
                connector=connector.name,
                error="OAuth request failed",
            )
            # Preserve verification only if this is still the same live grant.
            save_connector(
                connector, {"is_verified": True, "last_error": ""}, discard_stale=True
            )

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

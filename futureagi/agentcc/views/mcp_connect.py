"""Per-user upstream OAuth connect routes (M25x), under /agentcc/mcp/connect/.

- ``GET {server_id}/start/?binding=`` is where a gateway connect link points.
  It starts nothing and sets nothing: it sends the browser to the console,
  which signs the member in and makes the authenticated start. (The API
  authenticates by bearer token only, so a plain browser visit to it is never
  signed in.)
- ``POST {server_id}/start/`` (a member of the org) starts the connect for the
  member's own account and sets the ``__Host-`` cookie on that browser.
- ``GET callback/`` is the one redirect URI vendors call back (AM-C19). It
  reads no credential: the connect row and the cookie decide.
- ``GET client.json`` is our client metadata document (CIMD).
- ``DELETE {server_id}/`` disconnects the member's own account; with
  ``?mode=shared`` (an Owner or Admin) the organization's one account there.
- ``GET`` (the list) answers the member's own accounts on the org's
  per-person servers and whether a sign-in can start for each
  (``mcp_connections``). It fetches nothing and names no one else's account.

``POST {server_id}/start/`` with ``mode: shared`` signs in the
organization's one account on a shared server whose credential kind is
oauth (CN24): an Owner or Admin only.
"""

import html

import structlog
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc import mcp_features
from agentcc.mcp_personal_auth import (
    IsIndividualPrincipal,
    PersonalConnectionAuthentication,
)
from agentcc.permissions import is_gateway_admin
from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
)
from agentcc.serializers.mcp_connect import (
    MCPConnectStartRequestSerializer,
    MCPConnectStartResponseSerializer,
    MCPDisconnectQuerySerializer,
    MCPDisconnectResponseSerializer,
    MCPOwnConnectionListQuerySerializer,
    MCPOwnConnectionListResponseSerializer,
)
from agentcc.services import mcp_connections
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.mcp_connection_cleanup import upstream_outcome
from agentcc.services.mcp_credentials import SERVER_ID
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
    409: AgentccErrorResponseSerializer,
    502: AgentccErrorResponseSerializer,
}
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}
_REASONS = {
    "connected": "Connected. You can close this window and return to Future AGI.",
    "invalid_state": "This sign-in link was already used or is not valid.",
    "expired": "This sign-in took longer than 10 minutes.",
    "browser_mismatch": "This sign-in was started in another browser.",
    "denied": "The sign-in was cancelled at the server.",
    "connect_cancelled": "This sign-in was cancelled by Disconnect. Start a new sign-in.",
    "issuer_mismatch": "The sign-in came back from a different server.",
    "invalid_response": "The server's answer was incomplete.",
    "not_member": "You are no longer a member of this organization.",
    "individual_required": "Start a new sign-in from your own signed-in session.",
    "server_changed": "The MCP server changed while you were signing in.",
    "exchange_failed": "The server did not issue a token.",
    "client_changed": "The organization's OAuth app changed while you were signing in.",
    "not_admin": "Only an Owner or Admin signs in the organization's account.",
}


class _ConnectView(APIView):
    authentication_classes = [PersonalConnectionAuthentication]
    _gm = GeneralMethods()

    def _refused(self, refused: connect.ConnectRefused):
        return self._gm.custom_error_response(
            refused.status, str(refused), code=refused.code
        )


class MCPOwnConnectionListView(_ConnectView):
    """The member's own accounts on the org's per-person MCP servers."""

    permission_classes = [IsAuthenticated, IsIndividualPrincipal]

    @validated_request(
        query_serializer=MCPOwnConnectionListQuerySerializer,
        responses={200: MCPOwnConnectionListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
        reject_unknown_fields=True,
    )
    def get(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        try:
            rows = mcp_connections.own_connections(
                org, request.user, request.validated_query_data.get("server_id")
            )
        except connect.ConnectRefused as refused:
            return self._refused(refused)
        response = self._gm.success_response({"connections": rows})
        response["Cache-Control"] = "no-store"
        return response


class MCPConnectStartView(_ConnectView):
    """GET sends a connect link to the console; POST starts the connect."""

    def get_authenticators(self):
        # This public redirect starts nothing. Never run provisioning-capable
        # authentication merely because a connect link was opened.
        if self.request.method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self.request.method == "GET":
            return []
        return [IsAuthenticated(), IsIndividualPrincipal()]

    def get(self, request, server_id):
        if not SERVER_ID.fullmatch(server_id):
            return self._gm.not_found("No such MCP server.")
        binding = request.GET.get("binding", "")
        if len(binding) > connect._MAX_BINDING_CHARS:
            binding = ""
        response = HttpResponseRedirect(connect.console_connect_url(server_id, binding))
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    @validated_request(
        request_serializer=MCPConnectStartRequestSerializer,
        responses={200: MCPConnectStartResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, server_id):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        binding = request.validated_data.get("binding")
        mode = request.validated_data.get("mode") or "user"
        try:
            started = connect.start(
                org,
                request.user,
                server_id,
                binding,
                mode,
                individual_authenticated=True,
            )
        except connect.ConnectRefused as refused:
            return self._refused(refused)
        response = self._gm.success_response(
            {"authorize_url": started.authorize_url, "expires_in": started.max_age}
        )
        response["Cache-Control"] = "no-store"
        response.set_cookie(
            started.cookie_name,
            started.cookie_value,
            max_age=started.max_age,
            path="/",
            secure=True,
            httponly=True,
            samesite="Lax",
        )
        return response


class MCPDisconnectView(_ConnectView):
    permission_classes = [IsAuthenticated, IsIndividualPrincipal]

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        query_serializer=MCPDisconnectQuerySerializer,
        responses={200: MCPDisconnectResponseSerializer, **_ERRORS},
        request_methods=["DELETE"],
        reject_unknown_fields=True,
    )
    def delete(self, request, server_id):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        if mcp_features.hidden_server(org.pk, server_id):
            return mcp_features.not_found()
        mode = request.validated_query_data["mode"]
        if mode == "shared" and not is_gateway_admin(request):
            return self._gm.custom_error_response(
                403,
                "Only an Owner or Admin signs the organization's account out.",
                code="not_admin",
            )
        if not connect.is_member(request.user, org):
            return self._refused(
                connect.ConnectRefused(
                    "not_member", "not a member of this organization", 403
                )
            )
        found, synced, cleanup_pending = connect.disconnect(
            org, request.user, server_id, mode
        )
        if not found:
            if mode == "shared":
                return self._gm.not_found(
                    "The organization's account is not signed in on this MCP server."
                )
            return self._gm.not_found("You have no connection to this MCP server.")
        return self._gm.success_response(
            {
                "server_id": server_id,
                "disconnected": True,
                "gateway_synced": synced,
                "cleanup_pending": cleanup_pending,
                "upstream_revocation": upstream_outcome(
                    org, request.user, server_id, mode
                ),
            }
        )


@require_GET
def connect_callback(request):
    """The vendor's redirect back. Answers a page, never a redirect, so the
    code in this URL goes nowhere else."""
    if connect.callback_hidden(request.GET.get("state")):
        return mcp_features.not_found()
    result = connect.complete(request.GET, request.COOKIES)
    text = _REASONS.get(result.reason, "The sign-in did not complete.")
    if not result.ok:
        text = f"Not connected. {text} Start again from Future AGI."
    response = HttpResponse(
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Future AGI</title></head><body><p>"
        f"{html.escape(text)}</p></body></html>",
        status=200 if result.ok else 400,
        content_type="text/html; charset=utf-8",
    )
    for name, value in _PAGE_HEADERS.items():
        response[name] = value
    if result.cookie_name:
        response.delete_cookie(result.cookie_name, path="/", samesite="Lax")
    return response


@require_GET
def client_metadata_document(request):
    """Our client metadata document: the client id is this URL."""
    response = JsonResponse(connect.client_metadata())
    response["Cache-Control"] = "public, max-age=300"
    return response

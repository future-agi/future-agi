"""The org's own OAuth clients for catalog connectors (CN11), under
/agentcc/connectors/oauth-clients/.

An org Owner or Admin (``IsOrgGatewayAdmin``) stores the org's own OAuth
client for a client group (``client_group``, the catalog's
``auth.oauth2.clientGroup``) or for one of the org's MCP servers
(``server_id``). CN3's ``connector_oauth_clients.resolve`` prefers it to the
platform client. Every method is admin only, reads included: a member reads
neither the client id nor the key. The secret is write-only: it is sealed as
the request is validated and never appears in a response, a log line or an
error report, and it is never pushed to the gateway.
"""

import structlog
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND
from rest_framework.views import APIView

from agentcc import mcp_features
from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.services import mcp_credentials as vault
from agentcc.services.mcp_upstream_oauth import ConnectRefused
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)

_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
    409: AgentccErrorResponseSerializer,
}
_NOT_FOUND = "OAuth client not found."


class _SealedSecretField(serializers.Field):
    """A client secret, sealed (``vault.SealedSecret``) as it is validated,
    so the view and the service hold it only sealed. Its own checks (spaces,
    control characters) are the service's, which refuses it by name."""

    default_error_messages = {
        "invalid": "Must be a non-empty string.",
        "max_length": f"Must be at most {vault.MAX_SECRET_CHARS} characters.",
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("write_only", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str) or not data:
            self.fail("invalid")
        if len(data) > vault.MAX_SECRET_CHARS:
            self.fail("max_length")
        return vault.SealedSecret(data)


class OAuthClientCreateRequestSerializer(serializers.Serializer):
    client_group = serializers.CharField(max_length=40, required=False)
    server_id = serializers.CharField(max_length=64, required=False)
    client_id = serializers.CharField(
        max_length=vault.URL_MAX_LENGTH, trim_whitespace=False
    )
    client_secret = _SealedSecretField()
    label = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        if ("client_group" in attrs) == ("server_id" in attrs):
            raise serializers.ValidationError("Send one of client_group and server_id.")
        return attrs


class OAuthClientRotateRequestSerializer(serializers.Serializer):
    client_secret = _SealedSecretField()


class OAuthClientListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=200, required=False)


class OAuthClientSerializer(serializers.Serializer):
    """``vault.describe_oauth_client``: everything but the secret."""

    id = serializers.UUIDField()
    client_group = serializers.CharField(allow_null=True)
    server_id = serializers.CharField(allow_null=True)
    client_id = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    version = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    rotated_at = serializers.DateTimeField(allow_null=True)
    has_secret = serializers.BooleanField()


class OAuthClientResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = OAuthClientSerializer()


class OAuthClientListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = OAuthClientSerializer(many=True)


class _ClientPagination(ExtendedPageNumberPagination):
    page_size = 50
    max_page_size = 200


class _OAuthClientView(APIView):
    """Every method needs an org gateway admin."""

    permission_classes = [IsAuthenticated, IsOrgGatewayAdmin]
    _gm = GeneralMethods()

    def _client(self, request, credential_id):
        org = getattr(request, "organization", None)
        if org is None:
            return None
        row = vault.oauth_client_for_org(org, credential_id)
        if row is not None and not self._visible(vault.describe_oauth_client(row)):
            return None
        return row

    def _visible(self, data):
        if (
            not mcp_features.enabled(mcp_features.K2_APPS)
            and data.get("client_group") in mcp_features.K2_GROUPS
        ):
            return False
        if data.get("server_id"):
            return not mcp_features.hidden_server(
                self.request.organization.pk, data["server_id"]
            )
        return True

    def _refused(self, error):
        """The 4xx for a refused write: the message names fields, never a
        secret, and its code goes back to the client."""
        if isinstance(error, ConnectRefused):
            return self._gm.custom_error_response(error.status, str(error), code=error.code)
        if isinstance(error, vault.MCPServerNotFound):
            return self._gm.custom_error_response(
                HTTP_404_NOT_FOUND, str(error), code=error.code
            )
        return self._gm.bad_request(error)

    def _described(self, row, status=None):
        result = vault.describe_oauth_client(row)
        if status is None:
            return self._gm.success_response(result)
        return self._gm.success_response(result, status=status)


class OAuthClientListView(_OAuthClientView):
    @validated_request(
        query_serializer=OAuthClientListQuerySerializer,
        responses={200: OAuthClientListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
        reject_unknown_fields=True,
    )
    def get(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        paginator = _ClientPagination()
        page = paginator.paginate_queryset(
            [
                row
                for row in vault.oauth_clients_for_org(org)
                if self._visible(vault.describe_oauth_client(row))
            ],
            request,
            view=self,
        )
        return paginator.get_paginated_response(
            [vault.describe_oauth_client(row) for row in page]
        )

    @validated_request(
        request_serializer=OAuthClientCreateRequestSerializer,
        responses={201: OAuthClientResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        data = request.validated_data
        if not self._visible(data):
            return mcp_features.not_found()
        try:
            vault.require_oauth_client_write_enabled(
                org.pk, client_group=data.get("client_group"), server_id=data.get("server_id")
            )
            row = vault.create_oauth_client(
                org,
                client_group=data.get("client_group"),
                server_id=data.get("server_id"),
                client_id=data["client_id"],
                client_secret=data["client_secret"],
                actor=request.user,
                label=data["label"],
            )
        except (vault.MCPCredentialError, ConnectRefused) as e:
            return self._refused(e)
        return self._described(row, status=HTTP_201_CREATED)


class OAuthClientDetailView(_OAuthClientView):
    @validated_request(responses={200: OAuthClientResponseSerializer, **_ERRORS})
    def get(self, request, credential_id):
        row = self._client(request, credential_id)
        if row is None:
            return self._gm.not_found(_NOT_FOUND)
        return self._described(row)

    @validated_request(responses={200: OAuthClientResponseSerializer, **_ERRORS})
    def delete(self, request, credential_id):
        row = self._client(request, credential_id)
        if row is None:
            return self._gm.not_found(_NOT_FOUND)
        try:
            row = vault.revoke_oauth_client(row, request.user)
        except vault.MCPCredentialError as e:
            return self._refused(e)
        return self._described(row)


class OAuthClientRotateView(_OAuthClientView):
    @validated_request(
        request_serializer=OAuthClientRotateRequestSerializer,
        responses={200: OAuthClientResponseSerializer, **_ERRORS},
        reject_unknown_fields=True,
    )
    def post(self, request, credential_id):
        row = self._client(request, credential_id)
        if row is None:
            return self._gm.not_found(_NOT_FOUND)
        try:
            vault.require_oauth_client_write_enabled(row.organization_id, key=row.server_id)
            row = vault.rotate_oauth_client(
                row, request.validated_data["client_secret"], request.user
            )
        except (vault.MCPCredentialError, ConnectRefused) as e:
            return self._refused(e)
        return self._described(row)

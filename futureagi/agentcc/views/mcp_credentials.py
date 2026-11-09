"""The MCP credential vault API (M14), under /agentcc/mcp/credentials/.

Members of the org may list its credentials, which never carry a value; only
an org Owner or Admin (``IsOrgGatewayAdmin``) may write. The org is the
request's, so another org's credential answers 404.
"""

import structlog
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND
from rest_framework.views import APIView

from agentcc.mcp_personal_auth import IsIndividualPrincipal, is_individual_member
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
)
from agentcc.serializers.mcp_credentials import (
    MCPCredentialCreateRequestSerializer,
    MCPCredentialListQuerySerializer,
    MCPCredentialListResponseSerializer,
    MCPCredentialResponseSerializer,
    MCPCredentialRotateRequestSerializer,
    MCPUseManagedResponseSerializer,
)
from agentcc.services import mcp_credentials as vault
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.routing_key_rules import with_rejection_param
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)

_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
}
_NOT_FOUND = "MCP credential not found."


class _CredentialPagination(ExtendedPageNumberPagination):
    page_size = 50
    max_page_size = 200


class _VaultView(APIView):
    """Reads need a login in the org; writes an org gateway admin."""

    permission_classes = [IsAuthenticated]
    write_methods = frozenset({"POST", "DELETE"})
    _gm = GeneralMethods()

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method in self.write_methods:
            permissions.append(IsOrgGatewayAdmin())
        return permissions

    def _org(self, request):
        return getattr(request, "organization", None)

    def _person(self, request):
        if IsIndividualPrincipal().has_permission(
            request, self
        ) and is_individual_member(
            request.user,
            self._org(request),
            allow_legacy=getattr(request, "org_api_key", None) is None,
        ):
            return request.user
        return None

    def _credential(self, request, credential_id):
        org = self._org(request)
        if org is None:
            return None
        return (
            AgentccMCPCredential.objects.filter(pk=credential_id, organization=org)
            .filter(
                Q(scope="shared")
                | Q(scope="user", user=self._person(request), user__isnull=False)
            )
            .first()
        )

    def _refused(self, error):
        """The 4xx for a refused write: the error's message names fields,
        never a secret, and its code goes back to the client."""
        if isinstance(error, vault.MCPServerNotFound):
            return self._gm.custom_error_response(
                HTTP_404_NOT_FOUND, str(error), code=error.code
            )
        if isinstance(error, OrgConfigRejected):
            return with_rejection_param(self._gm.bad_request(error), error)
        return self._gm.bad_request(error)

    def _written(self, change, status=None):
        result = {
            **vault.describe(change.credential),
            "gateway_synced": change.gateway_synced,
        }
        if status is None:
            return self._gm.success_response(result)
        return self._gm.success_response(result, status=status)


class MCPCredentialListView(_VaultView):
    @validated_request(
        query_serializer=MCPCredentialListQuerySerializer,
        responses={200: MCPCredentialListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
        reject_unknown_fields=True,
    )
    def get(self, request):
        org = self._org(request)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        rows = vault.credentials_for_org(
            org,
            request.validated_query_data.get("server_id"),
            user=self._person(request),
        )
        paginator = _CredentialPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response([vault.describe(row) for row in page])

    @validated_request(
        request_serializer=MCPCredentialCreateRequestSerializer,
        responses={201: MCPCredentialResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request):
        data = request.validated_data
        try:
            change = vault.create(
                self._org(request),
                data["server_id"],
                kind=data["kind"],
                value=data["value"],
                header_name=data["header_name"] or None,
                placement=data.get("in"),
                name=data.get("name"),
                scope=data["scope"],
                actor=request.user,
                label=data["label"],
            )
        except (vault.MCPCredentialError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._written(change, status=HTTP_201_CREATED)


class MCPCredentialDetailView(_VaultView):
    @validated_request(responses={200: MCPCredentialResponseSerializer, **_ERRORS})
    def delete(self, request, credential_id):
        credential = self._credential(request, credential_id)
        if credential is None:
            return self._gm.not_found(_NOT_FOUND)
        try:
            change = vault.revoke(credential, request.user)
        except (vault.MCPCredentialError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._written(change)


class MCPCredentialRotateView(_VaultView):
    @validated_request(
        request_serializer=MCPCredentialRotateRequestSerializer,
        responses={200: MCPCredentialResponseSerializer, **_ERRORS},
        reject_unknown_fields=True,
    )
    def post(self, request, credential_id):
        credential = self._credential(request, credential_id)
        if credential is None:
            return self._gm.not_found(_NOT_FOUND)
        try:
            change = vault.rotate(
                credential,
                request.validated_data["value"],
                request.user,
                header_name=request.validated_data.get("header_name"),
                placement=request.validated_data.get("in"),
                name=request.validated_data.get("name"),
            )
        except (vault.MCPCredentialError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._written(change)


class MCPCredentialUseManagedView(_VaultView):
    """Move a ``fagi.*`` server to managed billing: the one explicit action
    that removes the record that the org had its own credential (decision 27).
    Written as a config version by the acting admin, and logged."""

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={200: MCPUseManagedResponseSerializer, **_ERRORS},
        reject_unknown_fields=True,
    )
    def post(self, request, server_id):
        try:
            result = vault.use_managed(self._org(request), server_id, request.user)
        except (vault.MCPCredentialError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._gm.success_response(result)

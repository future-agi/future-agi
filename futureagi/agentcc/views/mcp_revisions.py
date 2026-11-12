"""The MCP tool-revision API (M21), under /agentcc/mcp/revisions/.

The tools pinning holds for review (M20), what changed in each, and an
admin's decision. Members of the org may list them; only an org Owner or
Admin (``IsOrgGatewayAdmin``) may accept or reject one. The org is the
request's, so another org's revision answers 404. A list first brings the
rows up to date with the gateway; when the gateway cannot answer it shows the
rows last pulled, with ``gateway_synced: false``.
"""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.permissions import IsOrgGatewayAdmin, is_gateway_admin
from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
)
from agentcc.serializers.mcp_revisions import (
    MCPRevisionApproveBaselineRequestSerializer,
    MCPRevisionBaselineResponseSerializer,
    MCPRevisionDecisionResponseSerializer,
    MCPRevisionListQuerySerializer,
    MCPRevisionListResponseSerializer,
)
from agentcc.services import mcp_revisions as revisions
from agentcc.services.config_outcomes import mcp_credential_placement_refused_response
from agentcc.services.config_push import (
    MCPCredentialPlacementRefused,
    OrgConfigRejected,
)
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.guardrail_credentials import log_config_rejected
from agentcc.services.routing_key_rules import with_rejection_param
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
    409: AgentccErrorResponseSerializer,
}
_ORG_REQUIRED = "Organization context is required"


class _RevisionView(APIView):
    """Reads need a login in the org; writes an org gateway admin."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == "POST":
            permissions.append(IsOrgGatewayAdmin())
        return permissions

    def _org(self, request):
        return getattr(request, "organization", None)

    def _refused(self, error):
        if isinstance(error, revisions.RevisionError):
            return self._gm.custom_error_response(
                error.status, str(error), code=error.code
            )
        if isinstance(error, OrgConfigRejected):
            return with_rejection_param(self._gm.bad_request(error), error)
        return self._gm.bad_request(error)


class MCPRevisionListView(_RevisionView):
    @validated_request(
        query_serializer=MCPRevisionListQuerySerializer,
        responses={200: MCPRevisionListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
        reject_unknown_fields=True,
    )
    def get(self, request):
        org = self._org(request)
        if org is None:
            return self._gm.bad_request(_ORG_REQUIRED)
        synced = True
        warnings = []
        try:
            # Only an admin's list may write the baseline pins; the schedule
            # writes them for everyone else.
            result = revisions.pull(org, baseline=is_gateway_admin(request))
            synced, warnings = result["gateway_synced"], result["warnings"]
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("mcp_tool_revision_pull_rejected", org, e)
            return mcp_credential_placement_refused_response(self._gm, e)
        except (GatewayClientError, OrgConfigRejected) as e:
            synced = False
            logger.warning(
                "mcp_tool_revision_pull_failed",
                org_id=str(org.pk),
                error=type(e).__name__,
            )
        status = request.validated_query_data.get("status")
        rows = revisions.rows_for(org, None if status == "all" else status)
        return self._gm.success_response(
            {
                "revisions": [revisions.describe(row) for row in rows],
                "gateway_synced": synced,
                "warnings": warnings,
            }
        )


class MCPRevisionApproveView(_RevisionView):
    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={200: MCPRevisionDecisionResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, revision_id):
        org = self._org(request)
        if org is None:
            return self._gm.bad_request(_ORG_REQUIRED)
        try:
            result = revisions.approve(org, revision_id, request.user)
        except (revisions.RevisionError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._gm.success_response(result)


class MCPRevisionRejectView(_RevisionView):
    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={200: MCPRevisionDecisionResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, revision_id):
        org = self._org(request)
        if org is None:
            return self._gm.bad_request(_ORG_REQUIRED)
        try:
            result = revisions.reject(org, revision_id, request.user)
        except revisions.RevisionError as e:
            return self._refused(e)
        return self._gm.success_response(result)


class MCPRevisionApproveBaselineView(_RevisionView):
    @validated_request(
        request_serializer=MCPRevisionApproveBaselineRequestSerializer,
        responses={200: MCPRevisionBaselineResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request):
        org = self._org(request)
        if org is None:
            return self._gm.bad_request(_ORG_REQUIRED)
        try:
            result = revisions.approve_baseline(
                org, request.validated_data["server_id"], request.user
            )
        except (revisions.RevisionError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._gm.success_response(result)

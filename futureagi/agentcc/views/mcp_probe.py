"""``POST /agentcc/mcp/probe/``: test an upstream MCP URL through the gateway
(M14, the Django side of the console's "Test connection" button).

Org gateway admins only, for their own org (from the session, never the
body), at most 10 probes a minute per org. A typed ``auth`` is sent to the
gateway for this probe and is never stored or logged; a ``credential_id`` is
resolved from the org's vault and sent inline the same way, because the
gateway never takes an id (M17). A stored secret goes only where the org's
active config sends it: a ``credential_id`` probe of a shared credential the
config does not reference with state active (a save disconnected it), of any
URL but its server's, or of a ``fagi.*`` credential (whose URL is the
gateway's) is 400 ``mcp_probe_url_mismatch``, nothing is decrypted, and the
gateway is not called.
"""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_404_NOT_FOUND, HTTP_502_BAD_GATEWAY
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.serializers.mcp_credentials import (
    MCPProbeRequestSerializer,
    MCPProbeResponseSerializer,
)
from agentcc.services import mcp_credentials as vault
from agentcc.services.gateway_admin import mcp_probe
from agentcc.services.gateway_client import GatewayClientError
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

PROBE_RATE = "10/min"


class MCPProbeOrgThrottle(SimpleRateThrottle):
    """10 probes a minute per org, whoever in the org sends them."""

    scope = "agentcc_mcp_probe"
    rate = PROBE_RATE

    def get_cache_key(self, request, view):
        org = getattr(request, "organization", None)
        if org is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": org.pk}


class MCPProbeView(APIView):
    permission_classes = [IsAuthenticated, IsOrgGatewayAdmin]
    throttle_classes = [MCPProbeOrgThrottle]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=MCPProbeRequestSerializer,
        responses={
            200: MCPProbeResponseSerializer,
            400: AgentccErrorResponseSerializer,
            403: AgentccErrorResponseSerializer,
            404: AgentccErrorResponseSerializer,
            502: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        org = request.organization
        data = request.validated_data
        auth = data.get("auth")
        if "credential_id" in data:
            try:
                auth = vault.probe_auth(
                    org, data["credential_id"], request.user, data["url"]
                )
            except vault.MCPProbeURLMismatch as e:
                return self._gm.custom_error_response(400, str(e), code=e.code)
            if auth is None:
                # Another org's credential answers the same as none at all.
                return self._gm.custom_error_response(
                    HTTP_404_NOT_FOUND,
                    "MCP credential not found.",
                    code="mcp_credential_not_found",
                )
        try:
            result = mcp_probe.probe(
                str(org.pk),
                data["url"],
                transport=data.get("transport"),
                auth=dict(auth) if auth else None,
            )
        except mcp_probe.ProbeRefused as e:
            return self._gm.custom_error_response(
                400, str(e), code="invalid_probe_request"
            )
        except GatewayClientError as e:
            # The error names the path and status only; no body or secret.
            logger.warning("mcp_probe_gateway_error", org_id=str(org.pk), error=str(e))
            return self._gm.custom_error_response(
                HTTP_502_BAD_GATEWAY,
                "The gateway could not run the probe.",
                code="gateway_unreachable",
            )
        logger.info(
            "mcp_probe",
            org_id=str(org.pk),
            ok=result.get("ok"),
            error=result.get("error"),
            used_credential="credential_id" in data,
        )
        return self._gm.success_response(result)

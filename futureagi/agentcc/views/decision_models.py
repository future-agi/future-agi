"""The evaluator picker's decision catalog, scoped by its authenticated workspace."""

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.services import gateway_client
from agentcc.views._errors import exception_response
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class DecisionModelsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request):
        org = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        if (
            org is None
            or workspace is None
            or str(workspace.organization_id) != str(org.id)
        ):
            return self._gm.bad_request("Organization and workspace context are required")

        # Provider configuration belongs to the org. Use only the workspace's
        # authenticated org, never a browser-supplied org or gateway credential.
        try:
            listing = gateway_client.get_gateway_client().get_decision_models(str(org.id))
            response = self._gm.success_response(listing)
        except gateway_client.GatewayClientError as error:
            # A gateway auth/configuration failure is not an expired browser
            # session, nor a successful empty catalog (which means add a key).
            # A gateway older than the listing is named, not retryable: 501.
            if error.code == "decisions_not_supported":
                status = 501
            else:
                status = 503 if error.status_code in (None, 503) else 502
            response = self._gm.custom_error_response(
                status, error.public_message, code=error.code
            )
        except Exception as error:
            response = exception_response(
                self._gm,
                error,
                logger=logger,
                event="decision_models_list_failed",
                log_detail=False,
            )
        response["Cache-Control"] = "no-store"
        return response

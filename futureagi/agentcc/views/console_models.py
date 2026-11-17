"""GET /agentcc/console-models/: the Console's model list for the request's org.

The session's organization scopes it, as for every /agentcc/ console route.
For a person's own browser session the result also carries ``attestation``, a
signed statement of exactly these rows that FA's host page hands to the
Console; it is null for an API key or when there is none to give.
Contracts: lane-status gwc2197-CONSOLE-MODELS-CONTRACT-1114.md and
gwc2197-CONSOLE-RUN-SIGN-WIRE-1114.md section 3.
"""

from uuid import UUID

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.services.console_models import (
    ConsoleModelsUnavailable,
    list_console_models,
)
from agentcc.services.console_models_attestation import (
    AttestationUnavailable,
    issue_attestation,
)
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

UNAVAILABLE_MESSAGE = "The model list is temporarily unavailable. Please retry."
# Set by accounts.authentication for a live browser access session only; API
# key pairs, OAuth tool tokens and service identities never set it.
INTERACTIVE_SESSION = "interactive_session"


def _selected(request, header, actual):
    """False when a selection header names another org or workspace than the
    one the request resolved to (the host assertion is refused then too)."""
    value = request.headers.get(header)
    if value is None:
        return True
    try:
        return UUID(value) == UUID(str(actual))
    except (TypeError, ValueError, AttributeError):
        return False


def _attestation(request, organization, workspace, models):
    if getattr(request, "credential_provenance", None) != INTERACTIVE_SESSION:
        return None
    user_id = getattr(getattr(request, "user", None), "pk", None)
    if workspace is None or user_id is None:
        return None
    if not (
        _selected(request, "X-Organization-Id", organization.pk)
        and _selected(request, "X-Workspace-Id", workspace.pk)
    ):
        return None
    try:
        return issue_attestation(
            models,
            subject=str(user_id),
            organization_id=str(organization.pk),
            workspace_id=str(workspace.pk),
        )
    except AttestationUnavailable:
        logger.warning(
            "console_models_attestation_unavailable", org_id=str(organization.pk)
        )
        return None


class ConsoleModelsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(operation_description="The org's Console model list.")
    def get(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            response = self._gm.bad_request("No organization on the request")
        else:
            workspace = getattr(request, "workspace", None)
            try:
                result = dict(list_console_models(org, workspace))
                result["attestation"] = _attestation(
                    request, org, workspace, result["models"]
                )
                response = self._gm.success_response(result)
            except ConsoleModelsUnavailable as error:
                # An internal gateway failure is not the browser's session:
                # never 401/403 here.
                response = self._gm.custom_error_response(
                    error.status, UNAVAILABLE_MESSAGE, code="console_models_unavailable"
                )
        response["Cache-Control"] = "no-store"
        return response

"""The Console run routes.

- POST /agentcc/console-run/authorizations/: a run authorization for one
  Console chat, for the signed-in person's own browser session only.
- POST /agentcc/console-run/sign/: the agent service's grant and envelope for
  one gateway call, authenticated only by its Ed25519 client assertion (no
  session, no API key).
"""

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from agentcc.services.console_run_auth import ConsoleRunRefused, authorize_console_run
from agentcc.services.console_run_signer import SignRefused, sign_console_run
from tfc.utils.general_methods import GeneralMethods


class ConsoleRunAuthorizationsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        operation_description="A run authorization for one Console chat.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["models"],
            properties={
                "models": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    description="Models from the person's own Console model list.",
                    items=openapi.Items(type=openapi.TYPE_STRING, min_length=1),
                    min_items=1,
                    max_items=8,
                    unique_items=True,
                ),
            },
        ),
    )
    def post(self, request):
        if getattr(request, "organization", None) is None:
            response = self._gm.bad_request("No organization on the request")
        else:
            body = request.data if isinstance(request.data, dict) else {}
            try:
                result = authorize_console_run(request, body.get("models"))
                response = self._gm.success_response(result)
            except ConsoleRunRefused as refusal:
                response = self._gm.custom_error_response(
                    refusal.status, refusal.message, code=refusal.code
                )
        response["Cache-Control"] = "no-store"
        return response


class ConsoleRunSignView(APIView):
    # The client assertion is the only credential: no session or API key
    # authentication runs here, and so no CSRF or cookie applies.
    authentication_classes = []
    permission_classes = [AllowAny]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        operation_description="The grant and envelope for one Console run call.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["run_authorization", "route", "method", "body_sha256", "model"],
            additional_properties=False,
            properties={
                "run_authorization": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Run authorization issued for this agent service deployment.",
                ),
                "route": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    enum=["/-/delegated/v1/chat/completions"],
                ),
                "method": openapi.Schema(type=openapi.TYPE_STRING, enum=["POST"]),
                "body_sha256": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Lowercase hexadecimal SHA-256 digest of the gateway request body.",
                    pattern="^[0-9a-f]{64}$",
                    min_length=64,
                    max_length=64,
                ),
                "model": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="A model permitted by the run authorization.",
                    min_length=1,
                ),
            },
        ),
    )
    def post(self, request):
        try:
            response = self._gm.success_response(sign_console_run(request))
        except SignRefused as refusal:
            response = self._gm.custom_error_response(
                refusal.status, refusal.message, code=refusal.code
            )
        response["Cache-Control"] = "no-store"
        return response

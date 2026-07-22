from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication, LangfuseBasicAuthentication
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiDetailErrorResponseSerializer,
    ApiTextErrorResponseSerializer,
    HealthCheckResponseSerializer,
    LangfuseHealthResponseSerializer,
    LangfuseTracesResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods


class HealthCheckView(APIView):
    """
    Health check endpoint that returns 200 status when server is up.
    No authentication required for this endpoint.
    """

    _gm = GeneralMethods()

    @validated_request(
        responses={
            200: HealthCheckResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request, *args, **kwargs):
        """
        GET method for health check.
        Returns 200 OK with a simple status message.
        """
        return self._gm.success_response("Server is up and running")


class AuthenticatedHealthView(APIView):
    """Langfuse-compatible ``GET /api/public/health`` with authentication.

    Returns the same JSON shape as Langfuse::

        {"status": "OK", "version": "1.0.0"}

    When called with valid credentials (Basic Auth or API key headers)
    it returns 200.  Invalid / missing credentials return 401 via DRF's
    authentication layer.
    """

    authentication_classes = [LangfuseBasicAuthentication, APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: LangfuseHealthResponseSerializer,
            401: ApiDetailErrorResponseSerializer,
            403: ApiDetailErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request, *args, **kwargs):
        return Response(
            {"status": "OK", "version": "1.0.0"},
        )


class LangfuseCompatTracesView(APIView):
    """Langfuse-compatible ``GET /api/public/traces``.

    Vapi validates Langfuse credentials by calling this endpoint with
    ``?limit=1``.      Returns an empty list with standard pagination
    metadata so the credential check passes.
    """

    authentication_classes = [LangfuseBasicAuthentication, APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: LangfuseTracesResponseSerializer,
            401: ApiDetailErrorResponseSerializer,
            403: ApiDetailErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request, *args, **kwargs):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 1000)
        except (ValueError, TypeError):
            limit = 50
        return Response(
            {
                "data": [],
                "meta": {
                    "page": 1,
                    "limit": limit,
                    "total_items": 0,
                    "total_pages": 0,
                },
            },
        )

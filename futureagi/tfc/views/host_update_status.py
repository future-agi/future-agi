"""Read-only installation report; no host controls, probes, jobs or mutation."""

from django.conf import settings
from django.utils.cache import patch_vary_headers
from rest_framework.exceptions import NotFound, ParseError, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView, set_rollback

from accounts.middleware.require_2fa import Require2FAMiddleware
from tfc.host_updates.permissions import (
    CanReadHostUpdateStatus,
    HostUpdateAuthentication,
    supported_installation,
)
from tfc.host_updates.serializers import (
    HostUpdateStatusErrorSerializer,
    HostUpdateStatusOptionsSerializer,
    HostUpdateStatusResponseSerializer,
)
from tfc.host_updates.status import read_public_status
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope

ERROR_RESPONSES = dict.fromkeys(
    (400, 401, 403, 404, 405, 503), HostUpdateStatusErrorSerializer
)


class HostUpdateStatusView(APIView):
    authentication_classes = [HostUpdateAuthentication]
    permission_classes = [IsAuthenticated, CanReadHostUpdateStatus]
    renderer_classes = [JSONRenderer]
    http_method_names = ["get", "head", "options"]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Django middleware precedes DRF bearer authentication. Apply that same
        # policy to the now-authenticated, org-bound DRF request (not a bypass).
        self._two_factor_response = Require2FAMiddleware(lambda req: Response())(
            request
        )
        if self._two_factor_response.status_code == 403:
            raise PermissionDenied("2FA required", code="2fa_required")
        if request.query_params or request._request.read(1):
            raise ParseError("This endpoint accepts no query parameters or body.")
        self._topology = supported_installation()
        if self._topology is None:
            raise NotFound()

    @validated_request(
        responses={200: HostUpdateStatusResponseSerializer, **ERROR_RESPONSES},
        strict_response_validation=True,
    )
    def get(self, request, *args, **kwargs):
        response = read_public_status(
            configured_file=settings.FI_HOST_UPDATE_STATUS_FILE,
            # The slot binds the host registration. ENV_TYPE does not identify
            # its --production flag; retain that host-reported value unchanged.
            expected_installation={
                "slot_id": settings.FI_HOST_UPDATE_STATUS_SLOT,
                "topology": self._topology,
            },
        )
        serializer = HostUpdateStatusResponseSerializer(data=response)
        serializer.is_valid(raise_exception=True)
        # Keep the reconstructed plain wire tree. serializer.data wraps the
        # root in DRF ReturnDict, which our exact-type response validation must
        # reject just like any other non-wire mapping subclass.
        return Response(serializer.validated_data)

    @validated_request(responses={200: "No body", **ERROR_RESPONSES})
    def head(self, request, *args, **kwargs):
        # Authorize and scope, but never inspect operational data for metadata.
        return Response()

    @validated_request(
        responses={200: HostUpdateStatusOptionsSerializer, **ERROR_RESPONSES},
        strict_response_validation=True,
    )
    def options(self, request, *args, **kwargs):
        return Response(
            {"api_schema_version": 1, "methods": ["GET", "HEAD", "OPTIONS"]}
        )

    def handle_exception(self, exc):
        # The shared auth pipeline has legacy raw exception messages. None may
        # escape through this endpoint, including invalid credential/DB errors.
        status_code = getattr(exc, "status_code", 503)
        errors = {
            400: ("invalid", "This endpoint accepts no query parameters or body."),
            401: ("not_authenticated", "Authentication required."),
            403: ("permission_denied", "Host update status is not authorized."),
            404: ("not_found", "Not found."),
            405: ("method_not_allowed", "Method not allowed."),
            503: ("service_unavailable", "Status service unavailable."),
        }
        if status_code not in errors:
            status_code = 503
        code, detail = errors[status_code]
        if status_code == 403 and getattr(exc, "get_codes", None):
            special = exc.get_codes()
            if special == "2fa_required":
                code, detail = "2fa_required", "2FA required"
            elif special == "saml_scope_conflict":
                code, detail = (
                    "saml_scope_conflict",
                    "Organization selection is not authorized.",
                )
        set_rollback()
        # Only these constant messages/codes enter the common envelope. Never
        # pass exc or its detail/structured payload to the shared builder.
        response = Response(
            build_error_envelope(detail, status_code=status_code, code=code),
            status=status_code,
        )
        if status_code == 401:
            response["WWW-Authenticate"] = self.get_authenticate_header(self.request)
        response.exception = True
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Authorization", "X-Organization-Id"))
        if request.method != "HEAD":
            two_factor = getattr(self, "_two_factor_response", None)
            for header in ("X-2FA-Required", "X-2FA-Grace-Ends"):
                if two_factor is not None and header in two_factor:
                    response[header] = two_factor[header]
        else:
            response.data = None
        return response

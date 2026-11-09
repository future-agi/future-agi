"""One fail-closed availability boundary for Enterprise provisioning."""

from django.conf import settings
from rest_framework.exceptions import APIException


class ProvisioningUnavailable(APIException):
    status_code = 503
    default_code = "feature_unavailable"
    default_detail = "Enterprise credential provisioning is unavailable."


def provisioning_enabled():
    # Only an explicit boolean opt-in enables compatibility implementations.
    return getattr(settings, "EE_SERVICE_PROVISIONING_ENABLED", False) is True


def require_provisioning():
    if not provisioning_enabled():
        raise ProvisioningUnavailable()


class ProvisioningAvailabilityMixin:
    def handle_exception(self, exc):
        if isinstance(exc, ProvisioningUnavailable):
            from rest_framework.response import Response

            from tfc.utils.api_errors import build_error_envelope

            return Response(
                build_error_envelope(
                    str(exc.detail), status_code=503, code=exc.default_code, details={}
                ),
                status=503,
            )
        return super().handle_exception(exc)

    def initial(self, request, *args, **kwargs):
        # Refuse before authentication, tenant lookup, validation or replay recovery.
        require_provisioning()
        return super().initial(request, *args, **kwargs)

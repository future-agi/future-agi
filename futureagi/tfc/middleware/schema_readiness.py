from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from tfc.schema_readiness import RECHECK_SECONDS, SchemaState, schema_readiness
from tfc.utils.api_errors import build_error_envelope


class SchemaReadinessMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Process probes must not wait for post-install hooks: Helm --wait would
        # otherwise wait for the API before starting the job that migrates it.
        if (
            settings.STARTUP_SCHEMA_GUARD_ENABLED
            and request.path_info != "/health/"
            and request.method != "OPTIONS"
            and schema_readiness.check() is SchemaState.PENDING
        ):
            return JsonResponse(
                build_error_envelope(
                    "The platform is still initializing; retry shortly.",
                    status_code=503,
                    code="initializing",
                ),
                status=503,
                headers={"Retry-After": str(RECHECK_SECONDS)},
            )
        # UNAVAILABLE is not evidence of pending migrations. Preserve diagnostic
        # routes such as setup-checks rather than hiding database outages.
        return self.get_response(request)

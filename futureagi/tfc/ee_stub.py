import logging

from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_errors import build_error_envelope

_logger = logging.getLogger(__name__)


def _ee_stub(name, *, activity=False):
    """Return a callable that raises a clean, catchable error when invoked
    while ee/ is absent. Used as a module-level fallback for `from ee.*
    import X`.

    Args:
        name: symbol name, used in the error message.
        activity: when True, the returned callable raises a Temporal
            non-retryable ApplicationError so an activity invocation fails
            once instead of entering a retry storm. When False (default),
            raises FeatureUnavailable (HTTP 402 via the DRF handler).
    """

    def _raise(*args, **kwargs):
        _logger.warning(f"Could not load ee feature: {name}")
        # Lazy-import to avoid a circular dependency between tfc.ee_stub
        # and tfc.ee_gating at module load time.
        from tfc.ee_gating import _raise_denied

        _raise_denied(name, activity=activity)

    _raise.__name__ = name
    _raise.__qualname__ = name
    return _raise


def _ee_activity_stub(name):
    """Shorthand for `_ee_stub(name, activity=True)` — use inside Temporal
    activity modules so stub invocations fail non-retryably.
    """
    return _ee_stub(name, activity=True)


class EEFeatureNotAvailableView(APIView):
    authentication_classes = []
    permission_classes = []

    def _upgrade_response(self, request, *args, **kwargs):
        feature = kwargs.get("feature", "this feature")
        return Response(
            build_error_envelope(
                "This feature is not available. Upgrade your plan.",
                status_code=402,
                error_type="entitlement_error",
                code="ENTITLEMENT_DENIED",
                details={"feature": [feature]},
                extra={
                    "upgrade_required": True,
                },
            ),
            status=402,
        )

    get = _upgrade_response
    post = _upgrade_response
    put = _upgrade_response
    patch = _upgrade_response
    delete = _upgrade_response

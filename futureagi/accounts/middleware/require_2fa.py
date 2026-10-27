from datetime import timedelta

import structlog
from django.http import JsonResponse
from django.utils import timezone

from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)


def _user_has_2fa(user):
    """Check if user has 2FA via fresh DB queries (avoids stale cached relations)."""
    from accounts.models.totp_device import UserTOTPDevice
    from accounts.models.webauthn_credential import WebAuthnCredential

    has_totp = UserTOTPDevice.objects.filter(user=user, confirmed=True).exists()
    if has_totp:
        return True
    return WebAuthnCredential.objects.filter(user=user).exists()


class Require2FAMiddleware:
    """If the user's organization requires 2FA and the user doesn't have it,
    restrict access to non-2FA-setup endpoints after the grace period.
    """

    EXEMPT_PATHS = [
        "/accounts/2fa/",
        "/accounts/passkey/",
        "/accounts/passkeys/",
        "/accounts/user-info/",
        "/accounts/logout/",
        "/accounts/token/",
        "/accounts/organizations/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return self.get_response(request)

        # Skip for exempt paths
        if any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
            return self.get_response(request)

        org = getattr(request, "organization", None)
        if not org or not getattr(org, "require_2fa", False):
            return self.get_response(request)

        if _user_has_2fa(request.user):
            return self.get_response(request)

        # Check grace period
        if org.require_2fa_enforced_at:
            grace_end = org.require_2fa_enforced_at + timedelta(
                days=org.require_2fa_grace_period_days
            )
            if timezone.now() < grace_end:
                # Within grace period — allow access but add header
                response = self.get_response(request)
                response["X-2FA-Required"] = "grace-period"
                response["X-2FA-Grace-Ends"] = grace_end.isoformat()
                return response

        # Grace period expired — restrict access
        return JsonResponse(
            build_error_envelope(
                "2FA required",
                status_code=403,
                code="2fa_required",
                error_type="permission_error",
            ),
            status=403,
        )

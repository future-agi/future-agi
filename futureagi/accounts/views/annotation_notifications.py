"""User-facing endpoints for annotation digest preferences.

Three endpoints, all simple:

- ``POST /accounts/me/timezone/`` — body ``{"timezone": "America/Los_Angeles"}``.
  Called from the FE on each session boot using the browser's
  ``Intl.DateTimeFormat().resolvedOptions().timeZone`` so the daily digest
  can fire at the user's local morning. No-ops if value is unchanged.

- ``GET /accounts/notifications/unsubscribe/?token=...`` — one-click
  unsubscribe from both digest tracks. Token is HMAC-signed so links
  embedded in old emails can't be forged. Returns a plain HTML success
  page (no auth required — the token is the auth).

- ``GET /accounts/notifications/snooze/?token=...&days=7`` — snooze the
  realtime track for N days. Daily track keeps firing.
"""

from datetime import timedelta

import structlog
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)


def _is_valid_iana_tz(name: str) -> bool:
    """Best-effort IANA timezone validation. Tolerant of unknown zones
    (some browsers return non-IANA names like ``"Europe/Kyiv"`` on older
    OS versions)."""
    if not name or not isinstance(name, str):
        return False
    if len(name) > 64:
        return False
    try:
        import zoneinfo

        zoneinfo.ZoneInfo(name)
        return True
    except Exception:
        return False


class UserTimezoneView(APIView):
    """Capture the browser's IANA timezone for the authenticated user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        tz_name = (request.data or {}).get("timezone")
        if not _is_valid_iana_tz(tz_name):
            return Response(
                {"detail": "Invalid timezone."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = request.user
        if getattr(user, "last_timezone", None) != tz_name:
            user.last_timezone = tz_name
            user.save(update_fields=["last_timezone"])
        return Response({"timezone": tz_name}, status=status.HTTP_200_OK)


def _unsub_template(title: str, message: str) -> HttpResponse:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;"
        "background:#0a0a0a;color:#fafafa;display:flex;align-items:center;"
        "justify-content:center;min-height:100vh;margin:0;padding:24px}"
        ".card{max-width:480px;background:#171717;border:1px solid #27272a;"
        "border-radius:12px;padding:32px;text-align:center}"
        "h1{font-size:22px;margin:0 0 12px;font-weight:600}"
        "p{font-size:14px;color:#a1a1aa;line-height:1.6;margin:0}</style>"
        f"</head><body><div class='card'><h1>{title}</h1><p>{message}</p>"
        "</div></body></html>"
    )
    return HttpResponse(html, content_type="text/html")


class UnsubscribeAnnotationDigestView(APIView):
    """One-click unsubscribe from both digest tracks. Token is HMAC-signed."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.contrib.auth import get_user_model

        from model_hub.models.annotation_queues import AnnotationNotificationState
        from model_hub.utils.annotation_digest import _verify_unsubscribe_token

        token = request.query_params.get("token", "")
        user_id = _verify_unsubscribe_token(token)
        if not user_id:
            return _unsub_template(
                "Link expired",
                "This unsubscribe link is invalid or older than 30 days. "
                "Open your notification settings in the app to manage emails.",
            )
        try:
            user = get_user_model().objects.get(pk=user_id)
        except Exception:
            return _unsub_template(
                "Link invalid",
                "We couldn't find your account. The link may be malformed.",
            )
        state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
        state.digest_enabled = False
        state.save(update_fields=["digest_enabled", "updated_at"])
        return _unsub_template(
            "Unsubscribed",
            f"{user.email} will no longer receive annotation digest emails. "
            "You can re-enable them anytime in your notification settings.",
        )


class SnoozeAnnotationDigestView(APIView):
    """Snooze the realtime track for N days (default 7). Daily still fires."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.contrib.auth import get_user_model

        from model_hub.models.annotation_queues import AnnotationNotificationState
        from model_hub.utils.annotation_digest import _verify_unsubscribe_token

        token = request.query_params.get("token", "")
        user_id = _verify_unsubscribe_token(token)
        if not user_id:
            return _unsub_template(
                "Link expired",
                "This snooze link is invalid or older than 30 days. "
                "Open your notification settings in the app to manage emails.",
            )
        try:
            days = int(request.query_params.get("days", "7"))
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(days, 30))
        try:
            user = get_user_model().objects.get(pk=user_id)
        except Exception:
            return _unsub_template("Link invalid", "Account not found.")
        state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
        state.realtime_snoozed_until = timezone.now() + timedelta(days=days)
        state.save(update_fields=["realtime_snoozed_until", "updated_at"])
        return _unsub_template(
            "Snoozed",
            f"Realtime emails paused for {days} days. "
            "Daily digest will continue.",
        )

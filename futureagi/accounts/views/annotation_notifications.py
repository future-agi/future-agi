"""User-facing endpoints for annotation digest preferences.

Three endpoints, all simple:

- ``POST /accounts/me/timezone/`` — body ``{"timezone": "America/Los_Angeles"}``.
  Called from the FE on each session boot using the browser's
  ``Intl.DateTimeFormat().resolvedOptions().timeZone`` so the daily digest
  can fire at the user's local morning. No-ops if value is unchanged.

- ``GET /accounts/notifications/unsubscribe/?token=...`` — the digest
  unsubscribe link sent before per-category unsubscribe existed. It now opens
  the same confirmation page as the link below instead of acting on a GET.

- ``GET|POST /accounts/notifications/email/unsubscribe/?token=...`` — the
  link and ``List-Unsubscribe`` header every notification email carries (see
  ``tfc.utils.email_unsubscribe``). GET shows a confirmation page and changes
  nothing, because link scanners prefetch URLs; POST unsubscribes (a mail
  client's RFC 8058 one-click request, or the page's button), or resubscribes
  with ``action=resubscribe``. No auth: the signed token is the auth.

- ``GET /accounts/notifications/snooze/?token=...&days=7`` — snooze the
  realtime track for N days. Daily track keeps firing.
"""

from datetime import timedelta
from urllib.parse import quote

import structlog
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    EmailUnsubscribeRequestSerializer,
    TimezoneRequestSerializer,
    TimezoneResponseSerializer,
)
from tfc.utils import email_unsubscribe
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

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
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TimezoneRequestSerializer,
        responses={200: TimezoneResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        tz_name = request.validated_data["timezone"]
        if not _is_valid_iana_tz(tz_name):
            return self._gm.bad_request("Invalid timezone.")
        user = request.user
        if getattr(user, "last_timezone", None) != tz_name:
            user.last_timezone = tz_name
            user.save(update_fields=["last_timezone"])
        return Response({"timezone": tz_name}, status=status.HTTP_200_OK)


def _unsub_template(
    title: str, message: str, form: str = "", status_code: int = 200
) -> HttpResponse:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;"
        "background:#0a0a0a;color:#fafafa;display:flex;align-items:center;"
        "justify-content:center;min-height:100vh;margin:0;padding:24px}"
        ".card{max-width:480px;background:#171717;border:1px solid #27272a;"
        "border-radius:12px;padding:32px;text-align:center}"
        "h1{font-size:22px;margin:0 0 12px;font-weight:600}"
        "p{font-size:14px;color:#a1a1aa;line-height:1.6;margin:0}"
        "form{margin:24px 0 0}button{font:inherit;font-size:14px;font-weight:600;"
        "padding:12px 24px;border-radius:999px;border:0;cursor:pointer;"
        "background:#fafafa;color:#0a0a0a}</style>"
        f"</head><body><div class='card'><h1>{title}</h1><p>{message}</p>"
        f"{form}</div></body></html>"
    )
    return HttpResponse(html, content_type="text/html", status=status_code)


def _action_form(token: str, action: str, label: str) -> str:
    target = reverse("email-notification-unsubscribe")
    return (
        f"<form method='post' action='{target}?token={quote(token)}'>"
        f"<input type='hidden' name='action' value='{action}'>"
        f"<button type='submit'>{label}</button></form>"
    )


def _invalid_link(status_code: int = 200) -> HttpResponse:
    return _unsub_template(
        "Link invalid",
        "This unsubscribe link is invalid or older than 30 days. "
        "Use the Unsubscribe link in a recent email instead.",
        status_code=status_code,
    )


def _monitor_way_back(who: str) -> str:
    return (
        "You'll get these alerts again if the monitor's owner, or anyone who "
        f"can edit this monitor, adds {who} back to its recipients."
    )


def _nothing_to_change() -> HttpResponse:
    return _unsub_template(
        "Nothing to change",
        "The account or alert this link was for no longer exists, "
        "so there are no more of these emails to stop.",
    )


def _confirm_page(token: str, link: email_unsubscribe.Link) -> HttpResponse:
    """GET view of an unsubscribe link: shows the current state with the
    button that changes it, and changes nothing itself."""
    label = email_unsubscribe.CATEGORY_LABELS[link.category]
    who = escape(link.email)
    if not email_unsubscribe.destination_exists(link):
        return _nothing_to_change()
    if email_unsubscribe.is_subscribed(link):
        return _unsub_template(
            "Unsubscribe?",
            f"Stop sending {label} to {who}?",
            _action_form(token, "unsubscribe", "Unsubscribe"),
        )
    if link.category == email_unsubscribe.MONITOR_ALERTS:
        return _unsub_template(
            "You're unsubscribed",
            f"{who} doesn't receive {label}. {_monitor_way_back(who)}",
        )
    return _unsub_template(
        "You're unsubscribed",
        f"{who} doesn't receive {label}.",
        _action_form(token, "resubscribe", "Resubscribe"),
    )


_UNSUBSCRIBE_RESPONSES = {
    200: openapi.Response(
        "HTML unsubscribe page", schema=openapi.Schema(type=openapi.TYPE_STRING)
    ),
    **ACCOUNTS_ERROR_RESPONSES,
}


class EmailUnsubscribeView(APIView):
    """Per-category unsubscribe for notification emails. Token is signed."""

    permission_classes = [AllowAny]
    authentication_classes = []
    # Mail clients send the RFC 8058 one-click POST form-encoded or multipart.
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    @swagger_auto_schema(responses=_UNSUBSCRIBE_RESPONSES)
    def get(self, request):
        token = request.query_params.get("token", "")
        link = email_unsubscribe.read_token(token)
        if not link:
            return _invalid_link()
        return _confirm_page(token, link)

    @validated_request(
        request_serializer=EmailUnsubscribeRequestSerializer,
        responses=_UNSUBSCRIBE_RESPONSES,
        # The RFC 8058 body is ``List-Unsubscribe=One-Click``: not an error.
        reject_unknown_fields=False,
        validation_error_response=lambda errors: _invalid_link(
            status.HTTP_400_BAD_REQUEST
        ),
    )
    def post(self, request):
        token = request.query_params.get("token", "")
        link = email_unsubscribe.read_token(token)
        if not link:
            return _invalid_link(status.HTTP_400_BAD_REQUEST)
        subscribed = request.validated_data.get("action") == "resubscribe"
        is_monitor = link.category == email_unsubscribe.MONITOR_ALERTS
        label = email_unsubscribe.CATEGORY_LABELS[link.category]
        who = escape(link.email)

        result = email_unsubscribe.set_subscribed(link, subscribed)
        if result == email_unsubscribe.MISSING:
            return _nothing_to_change()
        if result == email_unsubscribe.REFUSED:
            return _unsub_template("Can't resubscribe", _monitor_way_back(who))
        if result == email_unsubscribe.CHANGED:
            logger.info(
                "email_notification_subscription_changed",
                category=link.category,
                subscribed=subscribed,
            )
        if is_monitor:
            return _unsub_template(
                "Unsubscribed",
                f"{who} will no longer receive {label}. {_monitor_way_back(who)}",
            )
        if subscribed:
            return _unsub_template(
                "Resubscribed",
                f"{who} will receive {label} again.",
                _action_form(token, "unsubscribe", "Unsubscribe"),
            )
        return _unsub_template(
            "Unsubscribed",
            f"{who} will no longer receive {label}.",
            _action_form(token, "resubscribe", "Resubscribe"),
        )


class UnsubscribeAnnotationDigestView(APIView):
    """Digest unsubscribe link from older emails. Token is HMAC-signed."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @swagger_auto_schema(
        responses={
            200: openapi.Response(
                "HTML unsubscribe result",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        from django.contrib.auth import get_user_model

        from model_hub.utils.annotation_digest import _verify_unsubscribe_token

        user_id = _verify_unsubscribe_token(request.query_params.get("token", ""))
        if not user_id:
            return _unsub_template(
                "Link expired",
                "This unsubscribe link is invalid or older than 30 days. "
                "Use the Unsubscribe link in a recent email instead.",
            )
        user = get_user_model().objects.filter(pk=user_id).first()
        if user is None:
            return _invalid_link()
        link = email_unsubscribe.Link(
            user.email.lower(), email_unsubscribe.ANNOTATION_DIGEST, None, str(user.pk)
        )
        token = email_unsubscribe.make_token(*link)
        return _confirm_page(token, link)


class SnoozeAnnotationDigestView(APIView):
    """Snooze the realtime track for N days (default 7). Daily still fires."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @swagger_auto_schema(
        responses={
            200: openapi.Response(
                "HTML snooze result",
                schema=openapi.Schema(type=openapi.TYPE_STRING),
            ),
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
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
            f"Realtime emails paused for {days} days. Daily digest will continue.",
        )

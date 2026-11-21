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

- ``GET|POST /accounts/notifications/snooze/?token=...&days=7`` — snooze the
  realtime track for N days. GET asks, POST snoozes. Daily track keeps firing.
"""

from datetime import timedelta

import structlog
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from drf_yasg import openapi
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer, StaticHTMLRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    EmailLinkQuerySerializer,
    EmailUnsubscribeRequestSerializer,
    SnoozeLinkQuerySerializer,
    SnoozeRequestSerializer,
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
    # Follows the reader's light or dark setting, and fits a phone screen.
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        f"<title>{title}</title>"
        "<style>:root{--bg:#fafafa;--card:#ffffff;--line:#e4e4e7;--text:#09090b;"
        "--muted:#52525b;--btn:#18181b;--btn-text:#fafafa}"
        "@media (prefers-color-scheme:dark){:root{--bg:#0a0a0a;--card:#171717;"
        "--line:#27272a;--text:#fafafa;--muted:#a1a1aa;--btn:#fafafa;"
        "--btn-text:#0a0a0a}}"
        "body{font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;"
        "background:var(--bg);color:var(--text);display:flex;align-items:center;"
        "justify-content:center;min-height:100vh;margin:0;padding:16px;"
        "box-sizing:border-box}"
        ".card{width:100%;max-width:480px;background:var(--card);"
        "border:1px solid var(--line);border-radius:8px;padding:24px;"
        "box-sizing:border-box;text-align:center}"
        "h1{font-size:20px;line-height:28px;margin:0 0 8px;font-weight:600}"
        "p{font-size:14px;line-height:22px;color:var(--muted);margin:0}"
        "form{margin:20px 0 0}button{font:inherit;font-size:14px;line-height:20px;"
        "font-weight:500;height:36px;padding:0 16px;border-radius:6px;border:0;"
        "cursor:pointer;background:var(--btn);color:var(--btn-text)}</style>"
        f"</head><body><main class='card'><h1>{title}</h1><p>{message}</p>"
        f"{form}</main></body></html>"
    )
    return HttpResponse(html, content_type="text/html", status=status_code)


def _action_form(action_url: str, action: str, label: str) -> str:
    return (
        f"<form method='post' action='{escape(action_url)}'>"
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
    if not email_unsubscribe.destination_exists(link):
        return _nothing_to_change()
    label = escape(email_unsubscribe.describe(link))
    who = escape(link.email)
    action_url = email_unsubscribe.page_url(token)
    if email_unsubscribe.is_subscribed(link):
        return _unsub_template(
            "Unsubscribe?",
            f"Stop sending {label} to {who}?",
            _action_form(action_url, "unsubscribe", "Unsubscribe"),
        )
    if link.category == email_unsubscribe.MONITOR_ALERTS:
        return _unsub_template(
            "You're unsubscribed",
            f"{who} doesn't receive {label}. {_monitor_way_back(who)}",
        )
    return _unsub_template(
        "You're unsubscribed",
        f"{who} doesn't receive {label}.",
        _action_form(action_url, "resubscribe", "Resubscribe"),
    )


def _html_responses(description: str):
    return {
        200: openapi.Response(
            description, schema=openapi.Schema(type=openapi.TYPE_STRING)
        ),
        400: openapi.Response(
            "HTML page: the link is invalid",
            schema=openapi.Schema(type=openapi.TYPE_STRING),
        ),
    }


def _invalid_link_400(errors):
    return _invalid_link(status.HTTP_400_BAD_REQUEST)


class _EmailedLinkView(APIView):
    """Public page an emailed link opens: no auth (the signed token is the
    auth), HTML out."""

    permission_classes = [AllowAny]
    authentication_classes = []
    renderer_classes = [StaticHTMLRenderer, JSONRenderer]
    # Page buttons and mail clients' RFC 8058 one-click POST send form data.
    parser_classes = [FormParser, MultiPartParser, JSONParser]


class EmailUnsubscribeView(_EmailedLinkView):
    """Per-category unsubscribe for notification emails. Token is signed."""

    @validated_request(
        query_serializer=EmailLinkQuerySerializer,
        responses=_html_responses("HTML unsubscribe confirmation page"),
        validation_error_response=lambda errors: _invalid_link(),
        security=[],
        produces=["text/html"],
    )
    def get(self, request):
        token = request.validated_query_data["token"]
        link = email_unsubscribe.read_token(token)
        if not link:
            return _invalid_link()
        return _confirm_page(token, link)

    @validated_request(
        request_serializer=EmailUnsubscribeRequestSerializer,
        query_serializer=EmailLinkQuerySerializer,
        responses=_html_responses("HTML unsubscribe result page"),
        # The RFC 8058 body is ``List-Unsubscribe=One-Click``: not an error.
        reject_unknown_fields=False,
        validation_error_response=_invalid_link_400,
        security=[],
        produces=["text/html"],
    )
    def post(self, request):
        token = request.validated_query_data["token"]
        link = email_unsubscribe.read_token(token)
        if not link:
            return _invalid_link(status.HTTP_400_BAD_REQUEST)
        action = request.validated_data.get("action")
        # Act only on the page's buttons or a mail client's one-click POST,
        # not on any POST that happens to carry the link.
        if not action and request.data.get("List-Unsubscribe") != "One-Click":
            return _invalid_link(status.HTTP_400_BAD_REQUEST)
        subscribed = action == "resubscribe"
        is_monitor = link.category == email_unsubscribe.MONITOR_ALERTS
        label = escape(email_unsubscribe.describe(link))
        who = escape(link.email)
        action_url = email_unsubscribe.page_url(token)

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
                _action_form(action_url, "unsubscribe", "Unsubscribe"),
            )
        return _unsub_template(
            "Unsubscribed",
            f"{who} will no longer receive {label}.",
            _action_form(action_url, "resubscribe", "Resubscribe"),
        )


class UnsubscribeAnnotationDigestView(_EmailedLinkView):
    """Digest unsubscribe link from older emails. Token is HMAC-signed."""

    @validated_request(
        query_serializer=EmailLinkQuerySerializer,
        responses=_html_responses("HTML unsubscribe confirmation page"),
        validation_error_response=lambda errors: _invalid_link(),
        security=[],
        produces=["text/html"],
    )
    def get(self, request):
        from django.contrib.auth import get_user_model

        from model_hub.utils.annotation_digest import _verify_unsubscribe_token

        user_id = _verify_unsubscribe_token(request.validated_query_data["token"])
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


def _snooze_expired() -> HttpResponse:
    return _unsub_template(
        "Link expired",
        "This snooze link is invalid or older than 30 days. "
        "Open your notification settings in the app to manage emails.",
    )


class SnoozeAnnotationDigestView(_EmailedLinkView):
    """Snooze the realtime track for N days (default 7). Daily still fires.
    GET asks first, because link scanners prefetch URLs; POST snoozes."""

    @staticmethod
    def _days(request) -> int:
        return max(1, min(request.validated_query_data.get("days") or 7, 30))

    @staticmethod
    def _user(request):
        from django.contrib.auth import get_user_model

        from model_hub.utils.annotation_digest import _verify_unsubscribe_token

        user_id = _verify_unsubscribe_token(request.validated_query_data["token"])
        if not user_id:
            return None
        return get_user_model().objects.filter(pk=user_id).first()

    @validated_request(
        query_serializer=SnoozeLinkQuerySerializer,
        responses=_html_responses("HTML snooze confirmation page"),
        validation_error_response=lambda errors: _snooze_expired(),
        security=[],
        produces=["text/html"],
    )
    def get(self, request):
        user = self._user(request)
        if user is None:
            return _snooze_expired()
        days = self._days(request)
        return _unsub_template(
            "Pause realtime emails?",
            f"Pause realtime annotation emails to {escape(user.email)} for "
            f"{days} days? The daily digest keeps coming.",
            _action_form(
                email_unsubscribe.public_api_url() + request.get_full_path(),
                "snooze",
                "Pause",
            ),
        )

    @validated_request(
        request_serializer=SnoozeRequestSerializer,
        query_serializer=SnoozeLinkQuerySerializer,
        responses=_html_responses("HTML snooze result page"),
        validation_error_response=lambda errors: _snooze_expired(),
        security=[],
        produces=["text/html"],
    )
    def post(self, request):
        from model_hub.models.annotation_queues import AnnotationNotificationState

        user = self._user(request)
        if user is None:
            return _snooze_expired()
        # Act only on the page's Pause button, not any POST carrying the link.
        if request.validated_data.get("action") != "snooze":
            return _snooze_expired()
        days = self._days(request)
        state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
        state.realtime_snoozed_until = timezone.now() + timedelta(days=days)
        state.save(update_fields=["realtime_snoozed_until", "updated_at"])
        return _unsub_template(
            "Snoozed",
            f"Realtime emails paused for {days} days. Daily digest will continue.",
        )

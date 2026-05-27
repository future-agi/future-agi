from __future__ import annotations

from django.http import HttpResponse
from django.utils.html import escape
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from accounts.models import OnboardingLifecycleSendLog
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.lifecycle_preferences import (
    snooze_onboarding_lifecycle,
    unsubscribe_onboarding_lifecycle,
)
from accounts.services.onboarding.lifecycle_tokens import verify_lifecycle_token


def _html_result(title, message):
    safe_title = escape(title)
    safe_message = escape(message)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;"
        "background:#0a0a0a;color:#fafafa;display:flex;align-items:center;"
        "justify-content:center;min-height:100vh;margin:0;padding:24px}"
        ".card{max-width:480px;background:#171717;border:1px solid #27272a;"
        "border-radius:12px;padding:32px;text-align:center}"
        "h1{font-size:22px;margin:0 0 12px;font-weight:600}"
        "p{font-size:14px;color:#a1a1aa;line-height:1.6;margin:0}</style>"
        f"</head><body><div class='card'><h1>{safe_title}</h1>"
        f"<p>{safe_message}</p></div></body></html>"
    )
    return HttpResponse(html, content_type="text/html")


def _send_log_from_token(token, kind):
    payload = verify_lifecycle_token(token, kind=kind)
    if not payload:
        return None
    return (
        OnboardingLifecycleSendLog.no_workspace_objects.select_related(
            "user",
            "organization",
            "workspace",
        )
        .filter(id=payload.get("send_log_id"))
        .first()
    )


def _record_preference_event(send_log, event_name, now, metadata=None):
    record_event(
        user=send_log.user,
        organization=send_log.organization,
        workspace=send_log.workspace,
        event_name=event_name,
        source="onboarding_lifecycle_email",
        product_path=send_log.primary_path,
        activation_stage=send_log.activation_stage,
        is_sample=False,
        occurred_at=now,
        metadata={
            "send_log_id": str(send_log.id),
            "evaluation_log_id": str(send_log.evaluation_log_id),
            "campaign_key": send_log.campaign_key,
            "campaign_family": send_log.campaign_group,
            **(metadata or {}),
        },
        idempotency_key=f"{event_name}:{send_log.id}",
    )


class OnboardingLifecycleUnsubscribeView(APIView):
    swagger_schema = None
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        send_log = _send_log_from_token(
            request.query_params.get("token", ""),
            "unsubscribe",
        )
        if not send_log:
            return _html_result(
                "Link expired",
                "This unsubscribe link is invalid or older than 30 days.",
            )
        preference = unsubscribe_onboarding_lifecycle(send_log=send_log)
        _record_preference_event(
            send_log,
            "lifecycle_email_unsubscribed",
            preference.unsubscribed_at,
        )
        return _html_result(
            "Unsubscribed",
            "Onboarding lifecycle emails are now turned off for this workspace.",
        )


class OnboardingLifecycleSnoozeView(APIView):
    swagger_schema = None
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        send_log = _send_log_from_token(
            request.query_params.get("token", ""),
            "snooze",
        )
        if not send_log:
            return _html_result(
                "Link expired",
                "This snooze link is invalid or older than 30 days.",
            )
        try:
            days = int(request.query_params.get("days", "7"))
        except (TypeError, ValueError):
            days = 7
        preference = snooze_onboarding_lifecycle(send_log=send_log, days=days)
        _record_preference_event(
            send_log,
            "lifecycle_email_snoozed",
            preference.snoozed_until,
            {"snoozed_until": preference.snoozed_until.isoformat()},
        )
        return _html_result(
            "Snoozed",
            f"Onboarding lifecycle emails are paused for {max(1, min(days, 30))} days.",
        )

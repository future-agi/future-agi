from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from accounts.models import OnboardingLifecycleSendLog
from accounts.services.onboarding.activation_events import has_event
from accounts.services.onboarding.lifecycle_sender import mark_lifecycle_send_clicked
from accounts.services.onboarding.lifecycle_tokens import verify_lifecycle_token


def _append_source_params(route, send_log):
    parts = urlsplit(route)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params.update(
        {
            "source": "onboarding_email",
            "campaign_key": send_log.campaign_key,
            "email_key": send_log.template_key,
            "target_event": send_log.target_success_event or "",
            "send_log_id": str(send_log.id),
        }
    )
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )


def resolve_lifecycle_click(token, *, now=None):
    payload = verify_lifecycle_token(token, kind="click")
    if not payload:
        return None, "/dashboard/home?source=onboarding_email&status=invalid"
    send_log = (
        OnboardingLifecycleSendLog.no_workspace_objects.select_related(
            "user",
            "organization",
            "workspace",
        )
        .filter(id=payload.get("send_log_id"))
        .first()
    )
    if not send_log:
        return None, "/dashboard/home?source=onboarding_email&status=missing"

    route = send_log.target_route or "/dashboard/home"
    stale_reason = None
    if send_log.target_success_event and has_event(
        organization=send_log.organization,
        workspace=send_log.workspace,
        event_name=send_log.target_success_event,
        is_sample=False,
    ):
        route = "/dashboard/home"
        stale_reason = "target_complete"
    if not route.startswith("/") or route.startswith("//"):
        route = "/dashboard/home"
        stale_reason = "route_unavailable"

    metadata = {"click_status": "stale" if stale_reason else "current"}
    if stale_reason:
        metadata["stale_reason"] = stale_reason
    mark_lifecycle_send_clicked(send_log, now=now, metadata=metadata)
    return send_log, _append_source_params(route, send_log)

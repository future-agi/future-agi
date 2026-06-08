from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from accounts.models import OnboardingLifecycleSendLog
from accounts.services.onboarding.lifecycle_completion import lifecycle_target_completed
from accounts.services.onboarding.lifecycle_sender import mark_lifecycle_send_clicked
from accounts.services.onboarding.lifecycle_tokens import verify_lifecycle_token


def _append_source_params(route, send_log, extra_params=None):
    parts = urlsplit(route)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    link_issued_at = send_log.sent_at or send_log.queued_at or send_log.created_at
    params.update(
        {
            "source": "onboarding_email",
            "campaign_key": send_log.campaign_key,
            "email_key": send_log.template_key,
            "target_stage": send_log.activation_stage,
            "target_event": send_log.target_success_event or "",
            "target_route": route,
            "send_log_id": str(send_log.id),
            "email_status": "current",
            "link_issued_at": link_issued_at.isoformat() if link_issued_at else "",
        }
    )
    if extra_params:
        params.update(extra_params)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )


def _campaign_for_send_log(send_log):
    return send_log.evaluation_log.registry_snapshot or {
        "campaign_key": send_log.campaign_key,
        "target_success_event": send_log.target_success_event,
        "sample_policy": "real_only",
    }


def _internal_route(route):
    return (
        isinstance(route, str) and route.startswith("/") and not route.startswith("//")
    )


def resolve_lifecycle_click(token, *, now=None):
    payload = verify_lifecycle_token(token, kind="click")
    if not payload:
        return None, "/dashboard/home?source=onboarding_email&status=invalid"
    send_log = (
        OnboardingLifecycleSendLog.no_workspace_objects.select_related(
            "evaluation_log",
            "user",
            "organization",
            "workspace",
        )
        .filter(id=payload.get("send_log_id"))
        .first()
    )
    if not send_log:
        return None, "/dashboard/home?source=onboarding_email&status=missing"

    route = send_log.target_route
    stale_reason = None
    if not _internal_route(route):
        route = "/dashboard/home"
        stale_reason = "route_unavailable"
    if lifecycle_target_completed(
        organization=send_log.organization,
        workspace=send_log.workspace,
        campaign=_campaign_for_send_log(send_log),
        target_success_event=send_log.target_success_event,
        after=send_log.sent_at or send_log.queued_at or send_log.created_at,
    ):
        route = "/dashboard/home"
        stale_reason = "target_complete"

    metadata = {"click_status": "stale" if stale_reason else "current"}
    if stale_reason:
        metadata["stale_reason"] = stale_reason
    mark_lifecycle_send_clicked(send_log, now=now, metadata=metadata)
    extra_params = None
    if stale_reason:
        extra_params = {
            "status": "stale",
            "email_status": "stale",
            "stale_reason": stale_reason,
        }
    return send_log, _append_source_params(route, send_log, extra_params)

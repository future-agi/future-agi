"""How an endpoint is shown (DESIGN.md sections 7.7, 9.1, 11.2 and R11): the URL (query masked) and header names
only for admins, never a secret or a header value; its health, and what waits on it."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from webhooks.api_validation import HTTP_URL_WARNING_MESSAGE
from webhooks.models import Delivery, Endpoint, EndpointState, Secret
from webhooks.permissions import MANAGE, VIEW_URL

SETTINGS_FIELDS = ("timeout_ms", "rate_limit_per_s", "max_in_flight", "max_age_s")


def backlog(ep: Endpoint) -> dict:
    """What waits on the endpoint (sections 7.7 and R11): due now and paused, with the oldest of each, how many
    paused ones are past the max age (they will not be sent on resume), and how many were skipped by the cap."""
    now = timezone.now()
    q = Delivery.objects.filter(endpoint_id=ep.id, is_test=False)
    due = q.filter(
        status__in=("pending", "retrying"), next_attempt_at__lte=now
    ).aggregate(n=Count("id"), oldest=Min("next_attempt_at"))
    paused = q.filter(status="paused").aggregate(
        n=Count("id"), oldest=Min("created_at"), newest=Max("created_at")
    )
    past = q.filter(
        status="paused", created_at__lt=now - timedelta(seconds=ep.max_age_s)
    ).count()
    capped = q.filter(status="skipped", skip_reason="backlog_cap").count()
    return {
        "due": due["n"],
        "oldest_due_at": due["oldest"],
        "paused": paused["n"],
        "oldest_paused_at": paused["oldest"],
        "paused_past_max_age": past,
        "skipped_backlog_cap": capped,
    }


def represent(caller, ep: Endpoint, *, detail: bool = False) -> dict:
    """The endpoint as the caller may see it: the URL (query masked) and header names only for admins (section 9.1);
    never a secret, never a header value."""
    admin_view = caller.can(VIEW_URL, ep.workspace_id)
    st = EndpointState.objects.filter(endpoint_id=ep.id).first()
    # url_display is saved by the validator and masks query values, not the scheme.
    scheme = urlsplit(ep.url_display or "").scheme.lower()
    scheme = scheme if scheme in ("http", "https") else None
    out = {
        "id": ep.id,
        "kind": ep.kind,
        "name": ep.name,
        "description": ep.description,
        "workspace_id": None if ep.workspace_id is None else str(ep.workspace_id),
        "org_wide": ep.workspace_id is None,
        "url_host": ep.url_host,
        "url_scheme": scheme,
        "url": ep.url_display if admin_view else None,
        "header_names": list(ep.header_names or []) if admin_view else None,
        "subscriptions": [
            {"event_type": s.event_type, "filters": s.filters}
            for s in ep.subscriptions.order_by("event_type")
        ],
        "status": ep.status,
        "disabled_reason": ep.disabled_reason or None,
        "disabled_at": ep.disabled_at,
        "payload_mode": ep.payload_mode,
        "include_people_names": ep.include_people_names,
        "accepts_run_targeting": ep.accepts_run_targeting,
        "api_version": ep.api_version,
        **{k: getattr(ep, k) for k in SETTINGS_FIELDS},
        "owner_id": None if ep.owner_id is None else str(ep.owner_id),
        "created_at": ep.created_at,
        "updated_at": ep.updated_at,
        "can_manage": caller.can(MANAGE, ep.workspace_id),
        "health": None
        if st is None
        else {
            "lane": st.lane,
            "consecutive_failed_attempts": st.consecutive_failed_attempts,
            "failing_since": st.failing_since,
            "last_success_at": st.last_success_at,
            "last_failure_at": st.last_failure_at,
            "last_status_code": st.last_status_code,
            "last_outcome": st.last_outcome or None,
        },
    }
    # Keep the warning beside the resource fields in every existing envelope,
    # including list rows whose URL is hidden. The message discloses no URL data.
    if scheme == "http":
        out["warnings"] = [
            {
                "code": "webhook_url_unencrypted",
                "message": HTTP_URL_WARNING_MESSAGE,
            }
        ]
    if detail:
        out["secrets"] = (
            [
                {
                    "id": s.id,
                    "tail": s.tail,
                    "state": s.state,
                    "created_at": s.created_at,
                    "expires_at": s.expires_at,
                }
                for s in Secret.objects.filter(endpoint_id=ep.id)
                .filter(
                    Q(state="active")
                    | Q(state="previous", expires_at__gt=timezone.now())
                )
                .order_by("-created_at")
            ]
            if admin_view
            else None
        )
        out["backlog"] = backlog(ep)
    return out

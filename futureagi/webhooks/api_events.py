"""The events list (DESIGN.md sections 7.6 and 10): the reconciliation path after any outage. Real events only
(never tests), thin ``data``, kept for the org policy's ``log_retention_days``, newest first, cursor pages."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from webhooks.api_deliveries import _subject_q, scope_q
from webhooks.models import Event, Policy
from webhooks.permissions import VIEW


def list_events(caller, params: dict) -> dict:
    """The reconciliation list (section 7.6): real events, thin data, kept for ``log_retention_days``."""
    caller_scope = scope_q(caller, VIEW, prefix="")
    org_policy = Policy.objects.filter(
        organization_id=caller.organization_id, workspace__isnull=True
    ).first()
    days = org_policy.log_retention_days if org_policy else 90
    qs = Event.objects.filter(
        organization_id=caller.organization_id,
        is_test=False,
        created_at__gte=timezone.now() - timedelta(days=days),
    ).filter(caller_scope)
    if params.get("type"):
        qs = qs.filter(type=params["type"])
    if params.get("subject"):
        qs = qs.filter(_subject_q(params["subject"], ""))
    if params.get("since"):
        qs = qs.filter(occurred_at__gte=params["since"])
    if params.get("until"):
        qs = qs.filter(occurred_at__lt=params["until"])
    if params.get("cursor"):
        qs = qs.filter(id__lt=params["cursor"])
    limit = params.get("limit") or 50
    rows = list(qs.order_by("-id")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "results": [
            {
                "id": e.id,
                "type": e.type,
                "api_version": e.api_version,
                "timestamp": e.occurred_at,
                "workspace_id": None if e.workspace_id is None else str(e.workspace_id),
                "subject": f"{e.subject_kind}:{e.subject_id}"
                if e.subject_kind
                else None,
                "data": e.data,
                "delivery_count": e.delivery_count,
            }
            for e in rows
        ],
        "next_cursor": rows[-1].id if more and rows else None,
    }

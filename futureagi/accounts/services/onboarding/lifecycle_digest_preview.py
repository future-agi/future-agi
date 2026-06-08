from __future__ import annotations

from accounts.models import OnboardingQualityAction
from accounts.services.onboarding.quality_actions import (
    DEFAULT_ACTION_FALLBACK_ROUTE,
    DEFAULT_ACTION_ROUTE,
    internal_route,
)

DIGEST_PREVIEW_ACTION_LIMIT = 5
DIGEST_PREVIEW_CAMPAIGNS = {"daily_quality_open_actions"}


def build_lifecycle_digest_preview(
    *,
    organization,
    workspace,
    activation_state,
    campaign,
    now,
    limit=DIGEST_PREVIEW_ACTION_LIMIT,
):
    if not organization or not workspace or not campaign:
        return None
    campaign_key = campaign.get("campaign_key")
    if campaign_key not in DIGEST_PREVIEW_CAMPAIGNS:
        return None
    if campaign_key == "daily_quality_open_actions":
        return _daily_quality_open_actions_preview(
            organization=organization,
            workspace=workspace,
            activation_state=activation_state,
            campaign=campaign,
            now=now,
            limit=limit,
        )
    return None


def _daily_quality_open_actions_preview(
    *,
    organization,
    workspace,
    activation_state,
    campaign,
    now,
    limit,
):
    primary_path = (
        activation_state.get("primary_path")
        or campaign.get("primary_path")
        or "observe"
    )
    queryset = OnboardingQualityAction.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        product_path=primary_path,
        status=OnboardingQualityAction.STATUS_OPEN,
        is_sample=False,
    ).order_by("due_at", "-last_event_at", "action_key")
    total_count = queryset.count()
    actions = [
        _preview_action(record=record, now=now)
        for record in queryset[: max(0, min(limit, DIGEST_PREVIEW_ACTION_LIMIT))]
    ]
    return {
        "kind": "daily_quality_open_actions",
        "campaign_key": campaign["campaign_key"],
        "template_key": campaign.get("template_key"),
        "generated_at": now.isoformat(),
        "source": "onboarding_quality_action",
        "primary_path": primary_path,
        "action_count": total_count,
        "omitted_count": max(total_count - len(actions), 0),
        "actions": actions,
        "safe_fields": [
            "action_id",
            "label",
            "route",
            "fallback_route",
            "source_type",
            "source_id",
            "assigned_to_user_id",
            "due_at",
            "is_overdue",
            "primary_path",
            "status",
            "age_minutes",
            "last_event_at",
        ],
    }


def _preview_action(*, record, now):
    last_event_at = record.last_event_at
    age_minutes = None
    if last_event_at:
        age_minutes = max(0, int((now - last_event_at).total_seconds() // 60))
    return {
        "action_id": record.action_key,
        "label": (record.label or "Review quality action")[:180],
        "route": record.route if internal_route(record.route) else DEFAULT_ACTION_ROUTE,
        "fallback_route": record.fallback_route
        if internal_route(record.fallback_route)
        else DEFAULT_ACTION_FALLBACK_ROUTE,
        "source_type": record.source_type or "workspace",
        "source_id": record.source_id or str(record.workspace_id),
        "assigned_to_user_id": str(record.assigned_to_id)
        if record.assigned_to_id
        else None,
        "due_at": record.due_at.isoformat() if record.due_at else None,
        "is_overdue": bool(record.due_at and record.due_at < now),
        "primary_path": record.product_path,
        "status": record.status,
        "age_minutes": age_minutes,
        "last_event_at": last_event_at.isoformat() if last_event_at else None,
    }

from __future__ import annotations

from collections import defaultdict

from django.utils import timezone

from accounts.models import (
    NotificationDeliveryLog,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
)

DIGEST_REVIEW_CAMPAIGNS = ("daily_quality_open_actions",)
DEFAULT_DIGEST_REVIEW_LIMIT = 25
MAX_DIGEST_REVIEW_LIMIT = 100


def _bounded_limit(limit):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_DIGEST_REVIEW_LIMIT
    return max(1, min(limit, MAX_DIGEST_REVIEW_LIMIT))


def _internal_route(route, fallback="/dashboard/home"):
    if (
        not isinstance(route, str)
        or not route.startswith("/")
        or route.startswith("//")
    ):
        return fallback
    return route


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_action(action):
    if not isinstance(action, dict):
        return None
    fallback_route = _internal_route(action.get("fallback_route"), "/dashboard/home")
    return {
        "action_id": str(action.get("action_id") or "")[:160],
        "label": str(action.get("label") or "")[:180],
        "route": _internal_route(action.get("route"), fallback_route),
        "fallback_route": fallback_route,
        "source_type": str(action.get("source_type") or "")[:64],
        "source_id": str(action.get("source_id") or "")[:128],
        "primary_path": str(action.get("primary_path") or "")[:32],
        "status": str(action.get("status") or "")[:32],
        "age_minutes": max(0, _safe_int(action.get("age_minutes"))),
        "last_event_at": action.get("last_event_at"),
        "assigned_to_user_id": (
            str(action.get("assigned_to_user_id"))
            if action.get("assigned_to_user_id")
            else None
        ),
        "due_at": action.get("due_at"),
        "is_overdue": bool(action.get("is_overdue")),
    }


def safe_digest_preview_from_metadata(preview, *, workspace_id=None):
    if not isinstance(preview, dict):
        return None
    actions = []
    for action in preview.get("actions") or []:
        safe_action = _safe_action(action)
        if safe_action and safe_action["action_id"] and safe_action["label"]:
            actions.append(safe_action)
    if not actions:
        return None
    omitted_count = preview.get("omitted_action_count")
    if omitted_count is None:
        omitted_count = preview.get("omitted_count")
    return {
        "kind": str(preview.get("kind") or "")[:64],
        "campaign_key": str(preview.get("campaign_key") or "")[:96],
        "template_key": str(preview.get("template_key") or "")[:96],
        "generated_at": preview.get("generated_at"),
        "workspace_id": str(preview.get("workspace_id") or workspace_id or "")[:64],
        "action_count": max(0, _safe_int(preview.get("action_count"), len(actions))),
        "omitted_action_count": max(0, _safe_int(omitted_count)),
        "actions": actions[:5],
    }


def _preview_summary(preview):
    actions = preview["actions"]
    return {
        "action_count": preview["action_count"],
        "visible_action_count": len(actions),
        "omitted_action_count": preview["omitted_action_count"],
        "overdue_count": sum(1 for action in actions if action["is_overdue"]),
        "assigned_count": sum(1 for action in actions if action["assigned_to_user_id"]),
    }


def _delivery_payloads_for_send_logs(send_logs):
    send_log_ids = [str(log.id) for log in send_logs]
    if not send_log_ids:
        return {}
    grouped = defaultdict(list)
    logs = NotificationDeliveryLog.no_workspace_objects.filter(
        source_type="onboarding_lifecycle",
        source_id__in=send_log_ids,
    ).order_by("-created_at")
    for log in logs:
        grouped[log.source_id].append(
            {
                "id": str(log.id),
                "channel": log.channel,
                "status": log.status,
                "suppressed_reason": log.suppressed_reason,
                "sent_at": log.sent_at,
                "created_at": log.created_at,
            }
        )
    return grouped


def _evaluation_item(log, preview):
    return {
        "source_type": "evaluation_log",
        "source_id": str(log.id),
        "campaign_key": log.campaign_key,
        "campaign_group": log.campaign_group,
        "template_key": log.template_key,
        "template_version": log.template_version,
        "status": log.status,
        "suppression_reason": log.suppression_reason,
        "user_id": str(log.user_id),
        "workspace_id": str(log.workspace_id),
        "evaluated_at": log.evaluated_at,
        "queued_at": None,
        "sent_at": None,
        "created_at": log.created_at,
        "preview": preview,
        "summary": _preview_summary(preview),
        "delivery_logs": [],
    }


def _send_item(log, preview, delivery_logs):
    return {
        "source_type": "send_log",
        "source_id": str(log.id),
        "campaign_key": log.campaign_key,
        "campaign_group": log.campaign_group,
        "template_key": log.template_key,
        "template_version": log.template_version,
        "status": log.status,
        "suppression_reason": log.suppression_reason,
        "user_id": str(log.user_id),
        "workspace_id": str(log.workspace_id) if log.workspace_id else None,
        "evaluated_at": log.evaluation_log.evaluated_at,
        "queued_at": log.queued_at,
        "sent_at": log.sent_at,
        "created_at": log.created_at,
        "preview": preview,
        "summary": _preview_summary(preview),
        "delivery_logs": delivery_logs.get(str(log.id), []),
    }


def digest_preview_review_payload(
    *,
    organization,
    workspace=None,
    campaign_key=None,
    limit=DEFAULT_DIGEST_REVIEW_LIMIT,
    now=None,
):
    now = now or timezone.now()
    limit = _bounded_limit(limit)
    campaign_keys = (campaign_key,) if campaign_key else DIGEST_REVIEW_CAMPAIGNS

    evaluation_logs = (
        OnboardingLifecycleEvaluationLog.no_workspace_objects.select_related(
            "user",
            "workspace",
        )
        .filter(organization=organization, campaign_key__in=campaign_keys)
        .order_by("-evaluated_at")
    )
    send_logs = (
        OnboardingLifecycleSendLog.no_workspace_objects.select_related(
            "evaluation_log",
            "user",
            "workspace",
        )
        .filter(organization=organization, campaign_key__in=campaign_keys)
        .order_by("-created_at")
    )
    if workspace:
        evaluation_logs = evaluation_logs.filter(workspace=workspace)
        send_logs = send_logs.filter(workspace=workspace)
    evaluation_logs = list(evaluation_logs[: limit * 2])
    send_logs = list(send_logs[: limit * 2])

    delivery_logs = _delivery_payloads_for_send_logs(send_logs)
    items = []
    for log in evaluation_logs:
        preview = safe_digest_preview_from_metadata(
            (log.metadata or {}).get("digest_preview"),
            workspace_id=log.workspace_id,
        )
        if preview:
            items.append(_evaluation_item(log, preview))
    for log in send_logs:
        preview = safe_digest_preview_from_metadata(
            (log.metadata or {}).get("digest_preview"),
            workspace_id=log.workspace_id or log.evaluation_log.workspace_id,
        )
        if preview:
            items.append(_send_item(log, preview, delivery_logs))

    items.sort(key=lambda item: item["created_at"], reverse=True)
    items = items[:limit]
    return {
        "generated_at": now,
        "limit": limit,
        "campaign_key": campaign_key or "",
        "count": len(items),
        "items": items,
    }

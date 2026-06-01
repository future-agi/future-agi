from __future__ import annotations

import requests
from django.utils import timezone

from accounts.models import (
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationPreference,
)
from accounts.services.onboarding.lifecycle_digest_review import (
    safe_digest_preview_from_metadata,
)
from accounts.services.onboarding.lifecycle_send_policy import (
    external_lifecycle_delivery_campaign_groups,
    external_lifecycle_delivery_channels,
)
from accounts.services.onboarding.lifecycle_template_context import (
    lifecycle_action_label,
)
from accounts.services.onboarding.lifecycle_template_contract import (
    lifecycle_email_copy_for_campaign,
)
from accounts.services.onboarding.notification_preferences import (
    notification_channel_delivery_config,
    notification_channels_for_delivery,
    notification_preference_decision,
    record_notification_delivery,
)
from accounts.services.onboarding.notification_registry import family_for_campaign_group

EXTERNAL_DELIVERY_TIMEOUT_SECONDS = 5


def _safe_text(value, *, fallback="", limit=180):
    if not isinstance(value, str):
        value = fallback
    value = " ".join(str(value or fallback).split())
    return value[:limit]


def _daily_quality_payload(send_log):
    preview = safe_digest_preview_from_metadata(
        (send_log.metadata or {}).get("digest_preview"),
        workspace_id=send_log.workspace_id,
    )
    if not preview:
        return None
    actions = preview["actions"]
    overdue_count = sum(1 for action in actions if action["is_overdue"])
    summary = {
        "action_count": preview["action_count"],
        "visible_action_count": len(actions),
        "omitted_action_count": preview["omitted_action_count"],
        "overdue_count": overdue_count,
    }
    return {
        "type": "daily_quality_digest",
        "family": NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
        "campaign_key": send_log.campaign_key,
        "template_key": send_log.template_key,
        "workspace_id": str(send_log.workspace_id) if send_log.workspace_id else None,
        "source": {
            "type": "onboarding_lifecycle",
            "id": str(send_log.id),
            "evaluation_log_id": str(send_log.evaluation_log_id),
        },
        "route_url": send_log.target_route,
        "summary": summary,
        "actions": actions[:5],
    }


def _product_onboarding_payload(send_log):
    campaign = getattr(send_log.evaluation_log, "registry_snapshot", None) or {}
    email_copy = lifecycle_email_copy_for_campaign(campaign)
    action_label = lifecycle_action_label(
        send_log.recommended_action_id,
        send_log.campaign_group,
    )
    return {
        "type": "product_onboarding",
        "family": NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        "campaign_key": send_log.campaign_key,
        "campaign_group": send_log.campaign_group,
        "template_key": send_log.template_key,
        "workspace_id": str(send_log.workspace_id) if send_log.workspace_id else None,
        "source": {
            "type": "onboarding_lifecycle",
            "id": str(send_log.id),
            "evaluation_log_id": str(send_log.evaluation_log_id),
        },
        "route_url": send_log.target_route,
        "summary": {
            "title": _safe_text(email_copy["subject"]),
            "action_label": _safe_text(action_label, fallback="Continue setup"),
            "primary_path": _safe_text(send_log.primary_path, limit=64),
            "activation_stage": _safe_text(send_log.activation_stage, limit=96),
        },
        "recommended_action": {
            "id": _safe_text(send_log.recommended_action_id, limit=96),
            "label": _safe_text(action_label, fallback="Continue setup"),
            "target_success_event": _safe_text(
                send_log.target_success_event,
                limit=96,
            ),
        },
    }


def _slack_payload(payload):
    if payload["type"] == "product_onboarding":
        summary = payload["summary"]
        text = f"FutureAGI setup: {summary['title']}"
        action_label = summary.get("action_label")
        if action_label:
            text += f". Next step: {action_label}"
        route = payload.get("route_url")
        if route:
            text += f". Open: {route}"
        return {"text": text}

    if payload["type"] == "usage_budget_threshold":
        summary = payload["summary"]
        text = (
            f"Usage budget {summary['stage_percent']}%: "
            f"{summary['budget_name']} is at {summary['current_usage']} "
            f"of {summary['threshold_value']}. Action: {summary['action_label']}"
        )
        route = payload.get("route_url")
        if route:
            text += f". Review: {route}"
        return {"text": text}

    count = payload["summary"]["action_count"]
    overdue = payload["summary"]["overdue_count"]
    route = payload["route_url"]
    first_actions = "\n".join(
        f"- {action['label']} ({action['status'] or 'open'})"
        for action in payload["actions"][:3]
    )
    text = f"Daily quality digest: {count} open action"
    if count != 1:
        text += "s"
    if overdue:
        text += f", {overdue} overdue"
    text += f". Review: {route}"
    if first_actions:
        text += f"\n{first_actions}"
    return {"text": text}


def post_notification_channel_payload(channel, payload):
    config = notification_channel_delivery_config(channel)
    if channel.type == NotificationChannel.TYPE_SLACK_WEBHOOK:
        url = config.get("webhook_url")
        body = _slack_payload(payload)
        headers = {}
    else:
        url = config.get("url")
        body = payload
        headers = {"content-type": "application/json"}
        if config.get("secret"):
            headers["x-futureagi-notification-token"] = str(config["secret"])[:256]
    if not url:
        raise ValueError("notification channel URL is missing")
    response = requests.post(
        url,
        json=body,
        headers=headers,
        timeout=EXTERNAL_DELIVERY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _post_channel_payload(channel, payload):
    post_notification_channel_payload(channel, payload)


def _external_delivery_error(exc):
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code:
        return f"{exc.__class__.__name__}: HTTP {status_code}"
    if isinstance(exc, ValueError):
        return str(exc)
    return exc.__class__.__name__


def _delivery_log(
    send_log,
    *,
    channel,
    status,
    now,
    channel_record=None,
    reason=None,
    error=None,
    metadata=None,
):
    family = family_for_campaign_group(send_log.campaign_group)
    channel_suffix = f":{channel_record.id}" if channel_record else ""
    return record_notification_delivery(
        organization=send_log.organization,
        workspace=send_log.workspace,
        user=send_log.user,
        family=family,
        source_type="onboarding_lifecycle",
        source_id=str(send_log.id),
        channel=channel,
        status=status,
        recipient_type=channel,
        recipient_identifier=(
            channel_record.display_name if channel_record else "configured channel"
        ),
        notification_key=send_log.campaign_key,
        idempotency_key=(
            f"onboarding_lifecycle:{send_log.id}:{channel}{channel_suffix}:{status}"
        ),
        stage=send_log.activation_stage,
        severity="info",
        suppressed_reason=reason,
        route_url=send_log.target_route,
        error=error,
        metadata={
            "campaign_group": send_log.campaign_group,
            "template_key": send_log.template_key,
            "target_success_event": send_log.target_success_event,
            **(metadata or {}),
        },
        now=now,
    )


def deliver_onboarding_lifecycle_external_channels(send_log, *, now=None):
    now = now or timezone.now()
    if send_log.campaign_group not in external_lifecycle_delivery_campaign_groups():
        return []
    family = family_for_campaign_group(send_log.campaign_group)
    if family == NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST:
        payload = _daily_quality_payload(send_log)
    elif family == NotificationPreference.FAMILY_PRODUCT_ONBOARDING:
        payload = _product_onboarding_payload(send_log)
    else:
        payload = None
    if not payload:
        return []

    logs = []
    for delivery_channel in external_lifecycle_delivery_channels():
        channels = notification_channels_for_delivery(
            organization=send_log.organization,
            workspace=send_log.workspace,
            channel=delivery_channel,
        )
        if not channels:
            continue
        decision = notification_preference_decision(
            organization=send_log.organization,
            workspace=send_log.workspace,
            user=send_log.user,
            family=family,
            channel=delivery_channel,
            now=now,
        )
        if not decision.allowed:
            logs.append(
                _delivery_log(
                    send_log,
                    channel=delivery_channel,
                    status=NotificationDeliveryLog.STATUS_SUPPRESSED,
                    now=now,
                    reason=decision.reason or "channel_disabled",
                    metadata={"preference_source": decision.source},
                )
            )
            continue
        for channel in channels:
            try:
                _post_channel_payload(channel, payload)
            except Exception as exc:
                logs.append(
                    _delivery_log(
                        send_log,
                        channel=delivery_channel,
                        status=NotificationDeliveryLog.STATUS_FAILED,
                        now=now,
                        channel_record=channel,
                        error=_external_delivery_error(exc),
                        metadata={"notification_channel_id": str(channel.id)},
                    )
                )
                continue
            logs.append(
                _delivery_log(
                    send_log,
                    channel=delivery_channel,
                    status=NotificationDeliveryLog.STATUS_SENT,
                    now=now,
                    channel_record=channel,
                    metadata={"notification_channel_id": str(channel.id)},
                )
            )
    return logs

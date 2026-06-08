from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from accounts.models import NotificationDeliveryLog, NotificationPreference
from accounts.services.onboarding.notification_delivery import (
    _external_delivery_error,
    post_notification_channel_payload,
)
from accounts.services.onboarding.notification_preferences import (
    notification_channels_for_delivery,
    notification_preference_decision,
    record_notification_delivery,
)
from tfc.utils.email import email_helper

BUDGET_SETTINGS_ROUTE = "/dashboard/settings/billing"
USAGE_BUDGET_THRESHOLDS = (50, 80, 100)
USAGE_BUDGET_THRESHOLD_ACTIONS = {
    50: "notify",
    80: "warn",
    100: "pause",
}
USAGE_BUDGET_NOTIFICATION_CHANNELS = (
    NotificationPreference.CHANNEL_EMAIL,
    NotificationPreference.CHANNEL_SLACK,
    NotificationPreference.CHANNEL_WEBHOOK,
)
USAGE_BUDGET_STAGE_SEVERITY = {
    50: "info",
    80: "warning",
    100: "critical",
}
USAGE_BUDGET_ACTION_LABELS = {
    "notify": "Notify",
    "warn": "Warn",
    "pause": "Pause usage",
}
USAGE_BUDGET_EMAIL_TEMPLATES = {
    "notify": "billing/usage_budget_notify.html",
    "warn": "billing/usage_budget_warn.html",
    "pause": "billing/usage_budget_paused.html",
}


def _safe_text(value, *, fallback="", limit=180):
    value = " ".join(str(value or fallback).split())
    return value[:limit]


def _usage_number(value, *, name):
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value).strip().replace(",", "")
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{name} must be numeric.") from exc
    if number < 0:
        raise ValueError(f"{name} cannot be negative.")
    return number


def usage_budget_threshold_stages_for_usage(
    *,
    current_usage,
    budget_limit,
    thresholds=USAGE_BUDGET_THRESHOLDS,
):
    current = _usage_number(current_usage, name="current_usage")
    limit = _usage_number(budget_limit, name="budget_limit")
    if limit <= 0:
        return ()
    crossed = []
    for threshold in sorted({int(value) for value in thresholds}):
        if threshold <= 0:
            continue
        threshold_usage = (limit * Decimal(threshold)) / Decimal(100)
        if current >= threshold_usage:
            crossed.append(threshold)
    return tuple(crossed)


def _route_url(route_url):
    return _safe_text(route_url or BUDGET_SETTINGS_ROUTE, limit=240)


def _base_url():
    return (
        getattr(settings, "USAGE_BILLING_BASE_URL", "")
        or getattr(settings, "BASE_URL", "")
        or ""
    ).rstrip("/")


def _email_recipients(recipients: Iterable[str] | None) -> list[str]:
    return [
        _safe_text(recipient, limit=254)
        for recipient in (recipients or [])
        if _safe_text(recipient, limit=254)
    ]


def _delivery_idempotency_key(payload, channel, channel_record=None):
    channel_suffix = f":{channel_record.id}" if channel_record else ""
    return (
        f"usage_budget:{payload['budget_id']}:{payload['summary']['period']}:"
        f"{payload['stage']}:{channel}{channel_suffix}"
    )


def _sent_delivery_log(*, organization, payload, channel, channel_record=None):
    return (
        NotificationDeliveryLog.no_workspace_objects.filter(
            organization=organization,
            idempotency_key=_delivery_idempotency_key(
                payload,
                channel,
                channel_record,
            ),
            status=NotificationDeliveryLog.STATUS_SENT,
        )
        .order_by("-sent_at", "-created_at")
        .first()
    )


def build_usage_budget_threshold_payload(
    *,
    budget_id,
    budget_name,
    scope,
    period,
    threshold_percent,
    threshold_value,
    current_usage,
    action,
    route_url=None,
    severity=None,
    metadata=None,
):
    stage_percent = int(threshold_percent)
    action = _safe_text(action or "notify", fallback="notify", limit=32)
    action_label = USAGE_BUDGET_ACTION_LABELS.get(action, action.title())
    return {
        "type": "usage_budget_threshold",
        "family": NotificationPreference.FAMILY_USAGE_BUDGET,
        "budget_id": _safe_text(budget_id, limit=96),
        "campaign_key": f"budget_threshold_{stage_percent}",
        "notification_key": f"budget_threshold_{stage_percent}",
        "stage": str(stage_percent),
        "severity": severity
        or USAGE_BUDGET_STAGE_SEVERITY.get(
            stage_percent,
            "warning",
        ),
        "route_url": _route_url(route_url),
        "summary": {
            "budget_name": _safe_text(budget_name, fallback="Usage budget"),
            "scope": _safe_text(scope, fallback="usage", limit=96),
            "period": _safe_text(period, limit=32),
            "stage_percent": stage_percent,
            "threshold_value": _safe_text(threshold_value, limit=64),
            "current_usage": _safe_text(current_usage, limit=64),
            "action": action,
            "action_label": action_label,
        },
        "metadata": {
            key: _safe_text(value, limit=160)
            for key, value in (metadata or {}).items()
            if key in {"dimension", "currency", "unit", "source"}
        },
    }


def _delivery_log(
    *,
    organization,
    workspace,
    user,
    payload,
    channel,
    status,
    now,
    channel_record=None,
    reason=None,
    error=None,
    recipient_identifier=None,
    metadata=None,
):
    recipient = (
        recipient_identifier
        if recipient_identifier is not None
        else channel_record.display_name
        if channel_record
        else "configured channel"
    )
    return record_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        source_type="usage_budget",
        source_id=payload["budget_id"],
        channel=channel,
        status=status,
        recipient_type=channel,
        recipient_identifier=recipient,
        notification_key=payload["notification_key"],
        idempotency_key=_delivery_idempotency_key(payload, channel, channel_record),
        stage=payload["stage"],
        severity=payload["severity"],
        suppressed_reason=reason,
        route_url=payload["route_url"],
        error=error,
        metadata={
            "scope": payload["summary"]["scope"],
            "period": payload["summary"]["period"],
            "threshold_value": payload["summary"]["threshold_value"],
            "current_usage": payload["summary"]["current_usage"],
            "action": payload["summary"]["action"],
            **(payload.get("metadata") or {}),
            **(metadata or {}),
        },
        now=now,
    )


def _email_subject(payload):
    summary = payload["summary"]
    if summary["action"] == "pause":
        return f"Usage paused: {summary['budget_name']}"
    if summary["action"] == "warn":
        return f"Usage warning: {summary['budget_name']}"
    return f"Budget threshold reached: {summary['budget_name']}"


def _email_context(payload):
    summary = payload["summary"]
    return {
        "base_url": _base_url(),
        "budget_name": summary["budget_name"],
        "scope": summary["scope"],
        "threshold": summary["threshold_value"],
        "current_usage": summary["current_usage"],
        "action": summary["action_label"],
    }


def _deliver_email(
    *,
    organization,
    workspace,
    user,
    payload,
    recipients,
    now,
):
    sent_log = _sent_delivery_log(
        organization=organization,
        payload=payload,
        channel=NotificationPreference.CHANNEL_EMAIL,
    )
    if sent_log:
        return sent_log

    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        channel=NotificationPreference.CHANNEL_EMAIL,
        now=now,
    )
    if not decision.allowed:
        return _delivery_log(
            organization=organization,
            workspace=workspace,
            user=user,
            payload=payload,
            channel=NotificationPreference.CHANNEL_EMAIL,
            status=NotificationDeliveryLog.STATUS_SUPPRESSED,
            now=now,
            reason=decision.reason or "channel_disabled",
            recipient_identifier=", ".join(recipients) or "configured recipients",
            metadata={"preference_source": decision.source},
        )
    if not recipients:
        return _delivery_log(
            organization=organization,
            workspace=workspace,
            user=user,
            payload=payload,
            channel=NotificationPreference.CHANNEL_EMAIL,
            status=NotificationDeliveryLog.STATUS_SUPPRESSED,
            now=now,
            reason="missing_recipient",
            recipient_identifier="configured recipients",
            metadata={"preference_source": decision.source},
        )

    template = USAGE_BUDGET_EMAIL_TEMPLATES.get(
        payload["summary"]["action"],
        USAGE_BUDGET_EMAIL_TEMPLATES["notify"],
    )
    try:
        email_helper(
            _email_subject(payload), template, _email_context(payload), recipients
        )
    except Exception as exc:
        return _delivery_log(
            organization=organization,
            workspace=workspace,
            user=user,
            payload=payload,
            channel=NotificationPreference.CHANNEL_EMAIL,
            status=NotificationDeliveryLog.STATUS_FAILED,
            now=now,
            recipient_identifier=", ".join(recipients),
            error=exc,
            metadata={"preference_source": decision.source},
        )
    return _delivery_log(
        organization=organization,
        workspace=workspace,
        user=user,
        payload=payload,
        channel=NotificationPreference.CHANNEL_EMAIL,
        status=NotificationDeliveryLog.STATUS_SENT,
        now=now,
        recipient_identifier=", ".join(recipients),
        metadata={"preference_source": decision.source},
    )


def _deliver_external_channel(
    *,
    organization,
    workspace,
    user,
    payload,
    channel,
    now,
):
    channels = notification_channels_for_delivery(
        organization=organization,
        workspace=workspace,
        channel=channel,
    )
    if not channels:
        return []
    decision = notification_preference_decision(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_USAGE_BUDGET,
        channel=channel,
        now=now,
    )
    if not decision.allowed:
        return [
            _delivery_log(
                organization=organization,
                workspace=workspace,
                user=user,
                payload=payload,
                channel=channel,
                status=NotificationDeliveryLog.STATUS_SUPPRESSED,
                now=now,
                reason=decision.reason or "channel_disabled",
                metadata={"preference_source": decision.source},
            )
        ]

    logs = []
    for channel_record in channels:
        sent_log = _sent_delivery_log(
            organization=organization,
            payload=payload,
            channel=channel,
            channel_record=channel_record,
        )
        if sent_log:
            logs.append(sent_log)
            continue
        try:
            post_notification_channel_payload(channel_record, payload)
        except Exception as exc:
            logs.append(
                _delivery_log(
                    organization=organization,
                    workspace=workspace,
                    user=user,
                    payload=payload,
                    channel=channel,
                    status=NotificationDeliveryLog.STATUS_FAILED,
                    now=now,
                    channel_record=channel_record,
                    error=_external_delivery_error(exc),
                    metadata={"notification_channel_id": str(channel_record.id)},
                )
            )
            continue
        logs.append(
            _delivery_log(
                organization=organization,
                workspace=workspace,
                user=user,
                payload=payload,
                channel=channel,
                status=NotificationDeliveryLog.STATUS_SENT,
                now=now,
                channel_record=channel_record,
                metadata={"notification_channel_id": str(channel_record.id)},
            )
        )
    return logs


def deliver_usage_budget_threshold_notifications_for_usage(
    *,
    organization,
    workspace=None,
    user=None,
    budget_id,
    budget_name,
    scope,
    period,
    budget_limit,
    current_usage,
    recipients=None,
    route_url=None,
    metadata=None,
    thresholds=USAGE_BUDGET_THRESHOLDS,
    actions_by_threshold=None,
    now=None,
):
    logs = []
    action_map = {
        **USAGE_BUDGET_THRESHOLD_ACTIONS,
        **(actions_by_threshold or {}),
    }
    for threshold in usage_budget_threshold_stages_for_usage(
        current_usage=current_usage,
        budget_limit=budget_limit,
        thresholds=thresholds,
    ):
        logs.extend(
            deliver_usage_budget_threshold_notification(
                organization=organization,
                workspace=workspace,
                user=user,
                budget_id=budget_id,
                budget_name=budget_name,
                scope=scope,
                period=period,
                threshold_percent=threshold,
                threshold_value=budget_limit,
                current_usage=current_usage,
                action=action_map.get(threshold, "warn"),
                recipients=recipients,
                route_url=route_url,
                metadata=metadata,
                now=now,
            )
        )
    return logs


def deliver_usage_budget_threshold_notification(
    *,
    organization,
    workspace=None,
    user=None,
    budget_id,
    budget_name,
    scope,
    period,
    threshold_percent,
    threshold_value,
    current_usage,
    action,
    recipients=None,
    route_url=None,
    severity=None,
    metadata=None,
    now=None,
):
    now = now or timezone.now()
    payload = build_usage_budget_threshold_payload(
        budget_id=budget_id,
        budget_name=budget_name,
        scope=scope,
        period=period,
        threshold_percent=threshold_percent,
        threshold_value=threshold_value,
        current_usage=current_usage,
        action=action,
        route_url=route_url,
        severity=severity,
        metadata=metadata,
    )
    logs = [
        _deliver_email(
            organization=organization,
            workspace=workspace,
            user=user,
            payload=payload,
            recipients=_email_recipients(recipients),
            now=now,
        )
    ]
    for channel in (
        NotificationPreference.CHANNEL_SLACK,
        NotificationPreference.CHANNEL_WEBHOOK,
    ):
        logs.extend(
            _deliver_external_channel(
                organization=organization,
                workspace=workspace,
                user=user,
                payload=payload,
                channel=channel,
                now=now,
            )
        )
    return logs

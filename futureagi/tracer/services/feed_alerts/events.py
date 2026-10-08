"""Transactional Error Feed issue events and Slack delivery drain."""

from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import quote

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager
from tfc.temporal import temporal_activity
from tracer.models.feed_alert import (
    ErrorFeedAlertDelivery,
    ErrorFeedAlertIssueState,
    ErrorFeedAlertRule,
    ErrorFeedIssueEvent,
    FeedAlertDeliveryStatus,
    FeedAlertTrigger,
)
from tracer.queries.feed import priority_to_severity

logger = structlog.get_logger(__name__)
_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def issue_snapshot(cluster) -> dict:
    return {
        "status": cluster.status,
        "severity": priority_to_severity(cluster.priority),
        "severity_assessment_status": cluster.severity_assessment_status,
        "severity_source": cluster.severity_source,
        "source": cluster.source,
        "issue_group": cluster.issue_group or "",
        "issue_category": cluster.issue_category or "",
        "occurrences": cluster.error_count,
        "title": (cluster.title or cluster.error_type or "")[:500],
        "cluster_id": cluster.cluster_id,
        "visible": not cluster.deleted,
    }


def record_issue_event(*, cluster, before: dict | None, source_key: str) -> None:
    """Call inside the same transaction as the authoritative issue write."""
    after = issue_snapshot(cluster)
    if before == after or not cluster.project.workspace_id:
        return
    if (
        not ErrorFeedAlertRule.no_workspace_objects.filter(
            organization_id=cluster.project.organization_id,
            workspace_id=cluster.project.workspace_id,
            enabled=True,
            deleted=False,
        )
        .filter(Q(project_id=cluster.project_id) | Q(project__isnull=True))
        .exists()
    ):
        return
    if not isinstance(source_key, str) or not 1 <= len(source_key) <= 255:
        raise ValueError("issue event needs a bounded idempotency key")
    ErrorFeedIssueEvent.no_workspace_objects.get_or_create(
        source_key=source_key,
        defaults={
            "organization_id": cluster.project.organization_id,
            "workspace_id": cluster.project.workspace_id,
            "project_id": cluster.project_id,
            "cluster": cluster,
            "event_kind": "new_issue" if before is None else "issue_changed",
            "before": before or {},
            "after": after,
        },
    )


def _matches(rule: ErrorFeedAlertRule, event: ErrorFeedIssueEvent) -> bool:
    before, after = event.before, event.after
    if not after.get("visible"):
        return False
    value = rule.trigger_value
    if rule.trigger_type == FeedAlertTrigger.NEW_ISSUE:
        trigger = event.event_kind == "new_issue" and not before
    elif rule.trigger_type == FeedAlertTrigger.SEVERITY_REACHED:
        # F6 starts at a placeholder medium; legacy clusters have a seed grade.
        assessed = after.get("severity_source") != "default"
        old_rank = _SEVERITY_RANK.get(before.get("severity"), 0)
        if before.get("severity_source") == "default":
            old_rank = 0
        trigger = bool(assessed) and old_rank < _SEVERITY_RANK[
            value
        ] <= _SEVERITY_RANK.get(after.get("severity"), 0)
    elif rule.trigger_type == FeedAlertTrigger.ESCALATING:
        trigger = (
            before.get("status") != "escalating" and after.get("status") == "escalating"
        )
    else:
        trigger = before.get("occurrences", 0) < value <= after.get("occurrences", 0)
    if not trigger:
        return False
    filter_fields = {
        "sources": "source",
        "severities": "severity",
        "statuses": "status",
        "issue_groups": "issue_group",
        "issue_categories": "issue_category",
    }
    return all(
        not rule.filters.get(key) or after.get(field) in rule.filters[key]
        for key, field in filter_fields.items()
    )


def _queue_event(event: ErrorFeedIssueEvent) -> None:
    with transaction.atomic():
        event = ErrorFeedIssueEvent.no_workspace_objects.select_for_update().get(
            pk=event.pk
        )
        if event.processed_at is not None:
            return
        rules = ErrorFeedAlertRule.no_workspace_objects.filter(
            organization_id=event.organization_id,
            workspace_id=event.workspace_id,
            enabled=True,
            deleted=False,
            created_at__lte=event.created_at,
        ).filter(Q(project_id=event.project_id) | Q(project__isnull=True))
        for rule in rules:
            if not _matches(rule, event):
                continue
            state, _ = ErrorFeedAlertIssueState.no_workspace_objects.get_or_create(
                rule=rule, cluster=event.cluster
            )
            state = (
                ErrorFeedAlertIssueState.no_workspace_objects.select_for_update().get(
                    pk=state.pk
                )
            )
            if (
                state.last_queued_at
                and event.created_at
                < state.last_queued_at + timedelta(seconds=rule.cooldown_seconds)
            ):
                continue
            delivery, created = (
                ErrorFeedAlertDelivery.no_workspace_objects.get_or_create(
                    rule=rule,
                    event=event,
                    channel_id=rule.slack_channel_id,
                    defaults={
                        "cluster": event.cluster,
                        "next_attempt_at": timezone.now(),
                    },
                )
            )
            if created:
                state.last_event = event
                state.last_queued_at = event.created_at
                state.save(update_fields=["last_event", "last_queued_at", "updated_at"])
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at", "updated_at"])


def _escape_slack(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _message(delivery: ErrorFeedAlertDelivery) -> tuple[str, list[dict]]:
    after = delivery.event.after
    rule = delivery.rule
    title = _escape_slack(
        " ".join(str(after.get("title") or "Error Feed issue").split())[:200]
    )
    raw_issue_id = str(after.get("cluster_id") or "")
    issue_id = _escape_slack(raw_issue_id)
    project_name = _escape_slack(str(delivery.event.project.name)[:100])
    severity = str(after.get("severity") or "unknown").lower()
    status = _escape_slack(
        str(after.get("status") or "unknown").replace("_", " ").title()
    )
    rule_name = _escape_slack(
        str(getattr(rule, "name", "") or rule.get_trigger_type_display())[:100]
    )
    severity_icon = {
        "critical": ":red_circle:",
        "high": ":red_circle:",
        "medium": ":large_yellow_circle:",
        "low": ":large_blue_circle:",
    }.get(severity, ":white_circle:")
    first_seen_at = getattr(
        getattr(delivery, "cluster", None), "first_seen", None
    ) or getattr(delivery.event, "created_at", None)
    if first_seen_at:
        timestamp = int(first_seen_at.timestamp())
        first_seen = f"<!date^{timestamp}^{{date_short_pretty}} at {{time}}|{first_seen_at:%Y-%m-%d %H:%M}>"
    else:
        first_seen = (
            "Just now"
            if getattr(delivery.event, "event_kind", "") == "new_issue"
            else "Unknown"
        )
    app_base = getattr(settings, "APP_BASE_URL", "").rstrip("/")
    issue_link = (
        f"{app_base}/dashboard/error-feed/{quote(raw_issue_id, safe='')}"
        if app_base
        else ""
    )
    alert_link = f"{app_base}/dashboard/error-feed/alerts" if app_base else ""
    headline = f"{severity_icon} " + (
        f"<{issue_link}|*{title}*>" if issue_link else f"*{title}*"
    )
    text = f"{severity_icon} Error Feed: {title} ({project_name}, {severity})"
    details = f"{headline}\nState: *{status}*   Severity: *{severity.title()}*   First seen: *{first_seen}*"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": details}}]
    if issue_link:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{issue_link}|Open issue in Error Feed>",
                },
            }
        )
    alert_label = f"<{alert_link}|{rule_name}>" if alert_link else rule_name
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Project: *{project_name}*    Alert: {alert_label}    Short ID: `{issue_id}`",
                }
            ],
        }
    )
    return text, blocks


def _send_delivery(delivery_id: uuid.UUID) -> None:
    retry_after = None
    with transaction.atomic():
        delivery = (
            ErrorFeedAlertDelivery.no_workspace_objects.select_for_update()
            .select_related("rule", "event", "event__project", "cluster")
            .get(pk=delivery_id)
        )
        if delivery.status not in {
            FeedAlertDeliveryStatus.PENDING,
            FeedAlertDeliveryStatus.RETRY,
            FeedAlertDeliveryStatus.SENDING,
        }:
            return
        if (
            delivery.status == FeedAlertDeliveryStatus.SENDING
            and delivery.updated_at > timezone.now() - timedelta(minutes=5)
        ):
            return
        delivery.status = FeedAlertDeliveryStatus.SENDING
        delivery.attempts += 1
        delivery.save(update_fields=["status", "attempts", "updated_at"])

    rule = delivery.rule
    connection = IntegrationConnection.no_workspace_objects.filter(
        pk=rule.slack_connection_id,
        organization_id=rule.organization_id,
        workspace_id=rule.workspace_id,
        platform=IntegrationPlatform.SLACK,
        status=ConnectionStatus.ACTIVE,
        deleted=False,
    ).first()
    if not rule.enabled or rule.deleted or connection is None:
        delivery.status = FeedAlertDeliveryStatus.FAILED
        delivery.error_code = "rule_or_integration_unavailable"
    else:
        from integrations.services.slack_service import SlackApiError, SlackService

        try:
            text, blocks = _message(delivery)
            credentials = CredentialManager.decrypt(
                bytes(connection.encrypted_credentials)
            )
            sent = SlackService().post_message(
                credentials, delivery.channel_id, text, blocks=blocks
            )
            delivery.status = FeedAlertDeliveryStatus.SENT
            delivery.slack_ts = str(sent.get("ts") or "")
            delivery.sent_at = timezone.now()
            delivery.error_code = ""
            ErrorFeedAlertRule.no_workspace_objects.filter(pk=rule.pk).update(
                last_triggered_at=delivery.sent_at
            )
        except SlackApiError as exc:
            delivery.error_code = str(exc.code)[:100]
            retry_after = exc.retry_after
            permanent = exc.code in {
                "invalid_auth",
                "account_inactive",
                "channel_not_found",
                "not_in_channel",
                "missing_scope",
            }
            delivery.status = (
                FeedAlertDeliveryStatus.FAILED
                if permanent or delivery.attempts >= 5
                else FeedAlertDeliveryStatus.RETRY
            )
        except Exception:
            logger.exception(
                "feed_alert_slack_send_failed", delivery_id=str(delivery.id)
            )
            delivery.error_code = "send_failed"
            delivery.status = (
                FeedAlertDeliveryStatus.FAILED
                if delivery.attempts >= 5
                else FeedAlertDeliveryStatus.RETRY
            )
    if delivery.status == FeedAlertDeliveryStatus.RETRY:
        delivery.next_attempt_at = timezone.now() + timedelta(
            seconds=max(
                min(3600, 30 * 2**delivery.attempts),
                min(3600, retry_after or 0),
            )
        )
    else:
        delivery.next_attempt_at = None
    delivery.save(
        update_fields=[
            "status",
            "slack_ts",
            "sent_at",
            "error_code",
            "next_attempt_at",
            "updated_at",
        ]
    )


@temporal_activity(max_retries=0, time_limit=120, queue="tasks_s")
def drain_feed_alerts() -> None:
    """Recover missed dispatch and process bounded event/delivery batches."""
    event_ids = list(
        ErrorFeedIssueEvent.no_workspace_objects.filter(processed_at__isnull=True)
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:100]
    )
    for event_id in event_ids:
        try:
            _queue_event(ErrorFeedIssueEvent.no_workspace_objects.get(pk=event_id))
        except Exception:
            logger.exception("feed_alert_event_failed", event_id=str(event_id))
    now = timezone.now()
    due_ids = list(
        ErrorFeedAlertDelivery.no_workspace_objects.filter(
            Q(
                status__in=[
                    FeedAlertDeliveryStatus.PENDING,
                    FeedAlertDeliveryStatus.RETRY,
                ],
                next_attempt_at__lte=now,
            )
            | Q(
                status=FeedAlertDeliveryStatus.SENDING,
                updated_at__lt=now - timedelta(minutes=5),
            )
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:100]
    )
    for delivery_id in due_ids:
        try:
            _send_delivery(delivery_id)
        except Exception:
            logger.exception("feed_alert_delivery_failed", delivery_id=str(delivery_id))

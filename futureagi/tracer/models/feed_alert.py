"""Workspace-owned Error Feed notification rules and durable delivery state."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from tfc.utils.base_model import BaseModel


class FeedAlertTrigger(models.TextChoices):
    NEW_ISSUE = "new_issue", "New issue"
    SEVERITY_REACHED = "severity_reached", "Severity reached"
    ESCALATING = "escalating", "Issue escalating"
    OCCURRENCES_CROSSED = "occurrences_crossed", "Occurrences crossed"


class FeedAlertDeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    RETRY = "retry", "Retry"
    FAILED = "failed", "Failed"


class ErrorFeedAlertRule(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.CASCADE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    project = models.ForeignKey(
        "tracer.Project", null=True, blank=True, on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    enabled = models.BooleanField(default=True)
    trigger_type = models.CharField(max_length=32, choices=FeedAlertTrigger.choices)
    trigger_value = models.JSONField(null=True, blank=True)
    filters = models.JSONField(default=dict, blank=True)
    slack_connection = models.ForeignKey(
        "integrations.IntegrationConnection", on_delete=models.PROTECT
    )
    slack_channel_id = models.CharField(max_length=80)
    slack_channel_name = models.CharField(max_length=255)
    cooldown_seconds = models.PositiveIntegerField(default=3600)
    last_triggered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_error_feed_alert_rule"
        indexes = [
            models.Index(
                fields=["organization", "workspace", "enabled"],
                name="feed_alert_scope_idx",
            ),
            models.Index(
                fields=["project", "trigger_type"], name="feed_alert_project_idx"
            ),
        ]


class ErrorFeedIssueEvent(BaseModel):
    """An immutable, committed before/after issue transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.CASCADE)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE)
    cluster = models.ForeignKey("tracer.TraceErrorGroup", on_delete=models.CASCADE)
    event_kind = models.CharField(max_length=32)
    source_key = models.CharField(max_length=255, unique=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_error_feed_issue_event"
        indexes = [
            models.Index(
                fields=["processed_at", "created_at"], name="feed_issue_event_due_idx"
            ),
            models.Index(
                fields=["cluster", "created_at"], name="feed_issue_event_cluster_idx"
            ),
        ]


class ErrorFeedAlertDelivery(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(ErrorFeedAlertRule, on_delete=models.CASCADE)
    event = models.ForeignKey(ErrorFeedIssueEvent, on_delete=models.CASCADE)
    cluster = models.ForeignKey("tracer.TraceErrorGroup", on_delete=models.CASCADE)
    channel_id = models.CharField(max_length=80)
    status = models.CharField(
        max_length=16,
        choices=FeedAlertDeliveryStatus.choices,
        default=FeedAlertDeliveryStatus.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    slack_ts = models.CharField(max_length=80, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_error_feed_alert_delivery"
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "event", "channel_id"],
                name="unique_feed_alert_delivery",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at"], name="feed_alert_delivery_due_idx"
            ),
            models.Index(
                fields=["rule", "cluster", "sent_at"],
                name="feed_alert_delivery_cool_idx",
            ),
        ]


class ErrorFeedAlertIssueState(BaseModel):
    """Serializes cooldown decisions per rule and issue under a row lock."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(ErrorFeedAlertRule, on_delete=models.CASCADE)
    cluster = models.ForeignKey("tracer.TraceErrorGroup", on_delete=models.CASCADE)
    last_event = models.ForeignKey(
        ErrorFeedIssueEvent, null=True, blank=True, on_delete=models.SET_NULL
    )
    last_queued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_error_feed_alert_issue_state"
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "cluster"],
                condition=Q(deleted=False),
                name="unique_feed_alert_issue_state",
            )
        ]

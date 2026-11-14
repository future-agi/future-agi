import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccWebhook(BaseModel):
    """Outbound webhook endpoint for event notifications."""

    EVENT_CHOICES = [
        ("request.completed", "Request Completed"),
        ("guardrail.triggered", "Guardrail Triggered"),
        ("budget.exceeded", "Budget Exceeded"),
        ("error.occurred", "Error Occurred"),
        ("batch.completed", "Batch Completed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_webhooks",
    )
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=2048)
    secret = models.CharField(max_length=255, blank=True, default="")
    events = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    headers = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True, default="")
    # Delivery health. db_default lets pods still on the old code keep
    # inserting webhooks while a deploy rolls out.
    consecutive_failures = models.IntegerField(default=0, db_default=0)
    disabled_reason = models.TextField(blank=True, default="", db_default="")
    disabled_at = models.DateTimeField(null=True, blank=True)
    dropped_events_total = models.PositiveBigIntegerField(default=0, db_default=0)
    last_dropped_at = models.DateTimeField(null=True, blank=True)
    # When the current failure streak began; cleared by a success.
    failing_since = models.DateTimeField(null=True, blank=True)
    # A dispatched delivery run holds this ahead of now: the dispatcher starts
    # no second run, and counts it toward AGENTCC_WEBHOOK_MAX_CONCURRENT_DELIVERIES.
    delivery_lease_until = models.DateTimeField(null=True, blank=True)
    # A run that stopped on failures pauses the webhook's next run until then.
    next_delivery_at = models.DateTimeField(null=True, blank=True)
    # When the dispatcher last started a delivery run for it. Slots rotate by
    # it (least recently served first), and a run releases its lease only
    # while this still names its own start.
    last_run_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_webhook"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_webhook_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
        ]

    def __str__(self):
        return f"{self.name} → {self.url}"


class AgentccWebhookEvent(BaseModel):
    """Individual webhook event delivery record."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    # Terminal: delivery never selects them and Retry refuses them.
    CANCELLED = "cancelled"  # its webhook was deleted
    EXPIRED = "expired"  # pending longer than AGENTCC_WEBHOOK_PENDING_MAX_AGE_HOURS
    DROPPED = "dropped"  # over AGENTCC_WEBHOOK_MAX_PENDING_PER_WEBHOOK

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (DELIVERED, "Delivered"),
        (FAILED, "Failed"),
        (DEAD_LETTER, "Dead Letter"),
        (CANCELLED, "Cancelled"),
        (EXPIRED, "Expired"),
        (DROPPED, "Dropped"),
    ]
    NOT_RETRYABLE = frozenset({DELIVERED, CANCELLED, EXPIRED, DROPPED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_webhook_events",
    )
    webhook = models.ForeignKey(
        AgentccWebhook,
        on_delete=models.CASCADE,
        related_name="webhook_events",
    )
    event_type = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
    )
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_response_code = models.IntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    next_retry_at = models.DateTimeField(null=True, blank=True)
    # When it was last queued again by hand (Retry). The pending age limit
    # counts from the later of this and created_at.
    requeued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_webhook_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["webhook"]),
            models.Index(fields=["status"]),
            models.Index(fields=["next_retry_at"]),
            models.Index(
                fields=["webhook", "status", "created_at"],
                name="agentcc_whevt_wh_status_ts",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="agentcc_whevt_status_ts",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} → {self.webhook.name} ({self.status})"

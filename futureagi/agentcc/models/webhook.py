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

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (DELIVERED, "Delivered"),
        (FAILED, "Failed"),
        (DEAD_LETTER, "Dead Letter"),
    ]

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

    class Meta:
        db_table = "agentcc_webhook_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["webhook"]),
            models.Index(fields=["status"]),
            models.Index(fields=["next_retry_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} → {self.webhook.name} ({self.status})"

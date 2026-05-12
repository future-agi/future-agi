import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccEmailAlert(BaseModel):
    """Email alert configuration for gateway event notifications."""

    PROVIDER_CHOICES = [
        ("sendgrid", "SendGrid"),
        ("resend", "Resend"),
        ("smtp", "SMTP"),
    ]

    EVENT_CHOICES = [
        ("budget.exceeded", "Budget Exceeded"),
        ("error.occurred", "Error Occurred"),
        ("error.rate_spike", "Error Rate Spike"),
        ("guardrail.triggered", "Guardrail Triggered"),
        ("latency.spike", "Latency Spike"),
        ("cost.threshold", "Cost Threshold"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_email_alerts",
    )
    name = models.CharField(max_length=255)
    recipients = models.JSONField(default=list, blank=True)
    events = models.JSONField(default=list, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)
    provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, default="sendgrid"
    )
    encrypted_config = models.BinaryField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    cooldown_minutes = models.IntegerField(default=5)
    last_triggered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_email_alert"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_email_alert_name_per_org",
            ),
        ]

    def __str__(self):
        return f"EmailAlert({self.name})"

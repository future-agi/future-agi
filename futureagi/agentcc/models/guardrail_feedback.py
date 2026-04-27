import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccGuardrailFeedback(BaseModel):
    """User feedback on guardrail decisions — correct, false positive, etc."""

    CORRECT = "correct"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    UNSURE = "unsure"

    FEEDBACK_CHOICES = [
        (CORRECT, "Correct"),
        (FALSE_POSITIVE, "False Positive"),
        (FALSE_NEGATIVE, "False Negative"),
        (UNSURE, "Unsure"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_guardrail_feedback",
    )
    request_log = models.ForeignKey(
        "prism.AgentccRequestLog",
        on_delete=models.CASCADE,
        related_name="guardrail_feedback",
    )
    check_name = models.CharField(max_length=255)
    feedback = models.CharField(
        max_length=20,
        choices=FEEDBACK_CHOICES,
    )
    comment = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_guardrail_feedback",
    )

    class Meta:
        db_table = "agentcc_guardrail_feedback"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["request_log"]),
            models.Index(fields=["check_name"]),
        ]

    def __str__(self):
        return f"{self.check_name}: {self.feedback}"

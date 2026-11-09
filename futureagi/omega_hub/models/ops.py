"""Operational records for the Omega Hub (outbox delivery and dead letters)."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class EmailOutboxStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    DEAD = "dead", "Dead letter"


class EmailOutbox(models.Model):
    """A committed email intent, delivered independently of the request thread.

    The rendered subject and body are frozen at enqueue time. This means retries do not
    depend on mutable database state and a maintainer decision can never be rolled back
    because an ESP is unavailable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.CharField(max_length=80)
    recipients = models.JSONField(default=list)
    subject = models.TextField()
    body = models.TextField()
    status = models.CharField(
        max_length=16, choices=EmailOutboxStatus.choices, default=EmailOutboxStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    dead_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "omega_hub_email_outbox"
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="omega_hub_email_due_idx"),
            models.Index(fields=["status", "created_at"], name="omega_hub_email_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.template} ({self.status}, attempt {self.attempts})"

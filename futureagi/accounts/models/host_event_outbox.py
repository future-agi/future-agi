"""The FA -> Omega EE revocation outbox (READINESS 1.2a, L06).

Each row is one access-relevant change to an organization membership, a workspace
membership or a user, captured by a Postgres trigger in the writer's own transaction
(``accounts.host_events.capture_sql``), so every write path is covered: ``save()``,
``QuerySet.update()`` (most member removals are bulk updates), ``_base_manager``,
cascades, raw SQL and the admin. The relay (``accounts.host_events.relay``) turns a row
into at most one signed event, delivers it and retries from here until EE accepts it.
"""

import uuid

from django.db import models


class HostEventStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    # Nothing to tell EE: the change granted access, or Omega is not configured here.
    SKIPPED = "skipped", "Skipped"


class HostEventOutbox(models.Model):
    id = models.BigAutoField(primary_key=True)
    # Stable across retries; EE refuses a second delivery of the same id as a replay.
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # organization_membership | workspace_membership | user
    source = models.CharField(max_length=32)
    operation = models.CharField(max_length=8)
    user_id = models.UUIDField()
    organization_id = models.UUIDField(null=True, blank=True)
    workspace_id = models.UUIDField(null=True, blank=True)
    old_state = models.JSONField(default=dict)
    new_state = models.JSONField(null=True, blank=True)
    captured_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=HostEventStatus.choices, default=HostEventStatus.PENDING
    )
    # The interpreted event, frozen at the first drain so every retry sends the same bytes.
    event = models.JSONField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    lease_until = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # The L06 receipt: when the delivered request left FA, and when EE says it received it.
    sent_at = models.DateTimeField(null=True, blank=True)
    ee_received_at_ms = models.BigIntegerField(null=True, blank=True)
    # A short code, never a response body or a secret.
    last_error = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "accounts_host_event_outbox"
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="host_event_due"),
            models.Index(fields=["status", "captured_at"], name="host_event_age"),
        ]

    def __str__(self):
        return f"{self.source} {self.operation} {self.user_id} ({self.status})"

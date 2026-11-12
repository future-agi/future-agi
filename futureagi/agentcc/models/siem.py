"""SIEM export (G16-SIEM): where an organization's MCP tool-call audit events
and spans go, and the queue of exports on their way there.

A destination is an org's own collector: a generic HTTPS webhook signed the
Standard Webhooks way, or an OTLP/HTTP collector (JSON encoding). It starts
disabled; only an org Owner or Admin changes one, and every change is audited.
Its secret (the webhook signing key, or the value of the OTLP auth header) is
encrypted at rest and never read back through the API.

A delivery is one batch of recorded calls for one destination and stream. It
holds event ids only, never a payload: the payload is rebuilt from the
current rows at send time, so a row deleted by retention is not exported and
the export never holds more than the closed metadata columns allow.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from agentcc.models.mcp_credential import EncryptedAtRestModel

STREAM_AUDIT_EVENTS = "audit_events"
STREAM_SPANS = "spans"
STREAMS = (STREAM_AUDIT_EVENTS, STREAM_SPANS)


class AgentccSIEMDestination(EncryptedAtRestModel):
    class Kind(models.TextChoices):
        WEBHOOK = "webhook"
        OTLP_HTTP = "otlp_http"
        HEC = "hec"

    ENCRYPTED_FIELDS = ("encrypted_secret",)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_siem_destinations",
        db_index=False,  # covered by the (organization, enabled) index
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # webhook: the endpoint itself; otlp_http: the collector's base URL, to
    # which /v1/traces and /v1/logs are added.
    url = models.CharField(max_length=2048)
    streams = models.JSONField(default=list)
    enabled = models.BooleanField(default=False)
    # otlp_http only: the header that carries the secret, e.g. Authorization.
    auth_header = models.CharField(max_length=64, blank=True, default="")
    encrypted_secret = models.TextField(null=True, blank=True)
    secret_hint = models.CharField(max_length=8, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_delivered_at = models.DateTimeField(null=True, blank=True)
    last_failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_siem_destination"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="unique_agentcc_siem_dest_name"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "enabled"], name="agentcc_siem_dest_org_idx"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.kind})"


class AgentccSIEMDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        DELIVERED = "delivered"
        DEAD_LETTER = "dead_letter"
        DISCARDED = "discarded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_siem_deliveries",
        db_index=False,  # covered by the (organization, status) index
    )
    destination = models.ForeignKey(
        AgentccSIEMDestination,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    stream = models.CharField(max_length=16)
    event_ids = models.JSONField(default=list)
    event_count = models.IntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=6)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_response_code = models.IntegerField(null=True, blank=True)
    # A status line or an error class only ("HTTP 503", "ConnectionError"):
    # never a response body, a URL or an exception message.
    last_error = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_siem_delivery"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "status", "-created_at"],
                name="agentcc_siem_dlv_org_idx",
            ),
            models.Index(
                fields=["status", "next_retry_at"], name="agentcc_siem_dlv_due_idx"
            ),
        ]

    def __str__(self):
        return f"{self.stream} -> {self.destination_id} ({self.status})"

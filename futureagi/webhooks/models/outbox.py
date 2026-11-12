"""The outbox and the delivery log: events, payload variants, deliveries and attempts (DESIGN.md sections
4.5-4.8)."""

from __future__ import annotations

from django.db import models

from webhooks.models.config import Endpoint
from webhooks.models.ids import (
    ID,
    new_attempt_id,
    new_delivery_id,
    new_event_id,
    new_payload_id,
)


class Event(models.Model):
    """The outbox. One row per producer change, unique per (organization, source_key) (R13)."""

    # An org-level event has no workspace; FA's thread-local auto-assignment must not invent one (see Endpoint).
    disable_auto_workspace_assignment = True

    id = models.CharField(default=new_event_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace", null=True, on_delete=models.CASCADE, related_name="+"
    )
    type = models.CharField(max_length=100)
    api_version = models.CharField(max_length=10, default="v1")
    occurred_at = models.DateTimeField()
    producer = models.CharField(max_length=60)
    subject_kind = models.CharField(max_length=30, blank=True, default="")
    subject_id = models.CharField(max_length=100, blank=True, default="")
    source_key = models.CharField(max_length=300)
    source_hash = models.CharField(max_length=64)
    data = models.JSONField()
    builder_input = models.JSONField(null=True)
    is_test = models.BooleanField(default=False)
    test_endpoint = models.ForeignKey(
        Endpoint, null=True, on_delete=models.CASCADE, related_name="+"
    )
    targeted_endpoint = models.ForeignKey(
        Endpoint, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    not_before = models.DateTimeField(null=True)
    FANOUT_STATE = [("pending", "pending"), ("done", "done"), ("failed", "failed")]
    fanout_state = models.CharField(
        max_length=10, choices=FANOUT_STATE, default="pending"
    )
    fanout_error = models.CharField(max_length=200, blank=True, default="")
    """Staff-only: why fan-out failed (``failed``: malformed data, no builder, a body over 64 KB). Never shown to a
    tenant; the event stays for an operator to inspect, and every other event goes on (R12)."""
    fanned_out_at = models.DateTimeField(null=True)
    delivery_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "webhooks_event"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "source_key"], name="evt_org_source_key"
            )
        ]
        indexes = [
            models.Index(
                fields=["fanout_state", "created_at"],
                name="evt_pending",
                condition=models.Q(fanout_state="pending"),
            ),
            models.Index(
                fields=["organization", "type", "occurred_at"], name="evt_org_type_time"
            ),
            # WH-1b (0002_operations): retention walks each org's events oldest first.
            models.Index(fields=["organization", "created_at"], name="evt_org_created"),
            # WH-1b (0002_operations): the health pass's fan-out windows (lag p95, WA3; failures, WA10) every minute.
            models.Index(
                fields=["fanned_out_at"],
                name="evt_fanned_out",
                condition=models.Q(fanned_out_at__isnull=False),
            ),
        ]


class Payload(models.Model):
    """One body per (event, mode, api_version, names) variant (R8); identical on every attempt."""

    id = models.CharField(default=new_payload_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="payloads")
    payload_mode = models.CharField(max_length=10)
    api_version = models.CharField(max_length=10, default="v1")
    include_people_names = models.BooleanField(default=True)
    body = models.BinaryField(null=True)
    body_sha256 = models.CharField(max_length=64)
    body_bytes = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "webhooks_payload"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "payload_mode", "api_version", "include_people_names"],
                name="whpl_variant",
            )
        ]


class Delivery(models.Model):
    STATUS = [
        (s, s)
        for s in (
            "pending",
            "sending",
            "retrying",
            "succeeded",
            "failed",
            "paused",
            "skipped",
            "cancelled",
        )
    ]

    id = models.CharField(default=new_delivery_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="deliveries"
    )
    endpoint = models.ForeignKey(
        Endpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    payload = models.ForeignKey(Payload, on_delete=models.CASCADE, related_name="+")
    seq = models.BigIntegerField(null=True)
    targeted = models.BooleanField(default=False)
    request_headers = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    retry_class = models.CharField(max_length=10, blank=True, default="")
    skip_reason = models.CharField(max_length=20, blank=True, default="")
    attempts_count = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    first_attempt_at = models.DateTimeField(null=True)
    succeeded_at = models.DateTimeField(null=True)
    final_outcome = models.CharField(max_length=30, blank=True, default="")
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "webhooks_delivery"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "endpoint"], name="dlv_event_endpoint"
            )
        ]
        indexes = [
            # The claim reads due rows per endpoint, oldest first, bounded by what the endpoint can take now.
            models.Index(
                fields=["endpoint", "next_attempt_at", "id"],
                name="dlv_endpoint_due",
                condition=models.Q(status__in=["pending", "retrying"]),
            ),
            models.Index(
                fields=["lease_until"],
                name="dlv_leased",
                condition=models.Q(status="sending"),
            ),
            models.Index(fields=["endpoint", "seq"], name="dlv_endpoint_seq"),
            # The destination registry's "Delivered (24 h)" and the log's time window read one endpoint's recent
            # deliveries (WH-3 F6): without it a registry read scans every delivery of the endpoint.
            models.Index(
                fields=["endpoint", "created_at"], name="dlv_endpoint_created"
            ),
        ]


class Attempt(models.Model):
    """The delivery log. ``resolved_ip`` holds public addresses only; ``staff_detail`` is never shown to a tenant."""

    id = models.CharField(default=new_attempt_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    delivery = models.ForeignKey(
        Delivery, on_delete=models.CASCADE, related_name="attempts"
    )
    n = models.IntegerField()
    trigger = models.CharField(max_length=10, default="auto")
    actor = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    t = models.BigIntegerField(null=True)
    signature_key_tails = models.JSONField(default=list)
    started_at = models.DateTimeField()
    dns_ms = models.IntegerField(null=True)
    ttfb_ms = models.IntegerField(null=True)
    duration_ms = models.IntegerField(null=True)
    resolved_ip = models.GenericIPAddressField(null=True)
    ip_family = models.SmallIntegerField(null=True)
    response_status = models.IntegerField(null=True)
    response_headers = models.JSONField(default=dict)
    response_body_excerpt = models.TextField(null=True)
    outcome = models.CharField(max_length=20)
    observed_outcome = models.CharField(max_length=20, blank=True, default="")
    error_detail = models.CharField(max_length=300, blank=True, default="")
    staff_detail = models.CharField(max_length=500, blank=True, default="")
    settled_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "webhooks_attempt"
        indexes = [
            models.Index(fields=["delivery", "n"], name="att_delivery_n"),
            # WH-1b (0002_operations): the health pass's attempt windows and the retention purge.
            models.Index(fields=["settled_at"], name="att_settled"),
        ]

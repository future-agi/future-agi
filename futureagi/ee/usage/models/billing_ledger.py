"""Tables of the one billing ledger beside ``ManagedCreditEntry`` (PLAN 1.2).

- ``UsageEventRecord``: the usage-event-v1 columns of one ``UsageEventLog`` row
  (a one-to-one side table: ``models/usage.py`` is not this lane's file). It
  holds the rater's outcome: exactly one of ``rated_entry`` and
  ``not_billable_reason`` once rated (I1).
- ``UsageEventRefusal``: every refused ingest item, never dropped (OR:161.1).
- ``LedgerServiceCredential`` and ``LedgerCredentialBinding``: the service
  credential of the out-of-process ingest, bound to systems, products, orgs
  and (for ``ee_daemon``) workspaces. Only the token's SHA-256 is stored.
- ``StripeEventLog``: one row per Stripe ``event.id`` (BL-STRIPE writes it).
- ``BillingInvoiceMirror``: Stripe invoices keyed (mode, invoice id) (G4).
- ``ReconciliationRun``: one row per invariant check run.

Stripe is never called from here; BL-STRIPE passes plain values.
"""

import uuid

from django.db import models
from django.utils import timezone

from accounts.models.organization import Organization
from ee.usage.models.managed_credits import ManagedCreditEntry
from ee.usage.models.usage import UsageEventLog

NOT_BILLABLE_REASONS = (
    "legacy_period",
    "legacy_ee",
    "legacy_period_closed",
    "failed_infra",
    "marketplace_dimension_unmapped",
)


class UsageEventRecord(models.Model):
    """usage-event-v1 section 5 columns and the rating outcome of one event."""

    event = models.OneToOneField(
        UsageEventLog,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="ledger_record",
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+", db_index=False
    )
    # UsageEventLog.event_id (the attribute ``event_id`` is the row's own key).
    event_uuid = models.UUIDField(unique=True)
    product = models.CharField(max_length=16, blank=True, default="")
    unit = models.CharField(max_length=32, blank=True, default="")
    source_system = models.CharField(max_length=32, blank=True, default="")
    source_ref = models.CharField(max_length=255, blank=True, default="")
    agent_id = models.CharField(max_length=128, blank=True, default="")
    agent_run_id = models.CharField(max_length=128, blank=True, default="")
    api_key_id = models.CharField(max_length=255, blank=True, default="")
    purpose = models.CharField(max_length=16, blank=True, default="")
    request_id = models.CharField(max_length=255, blank=True, default="")
    # Empty for rows the legacy consumer wrote (no v1 payload).
    payload_sha256 = models.CharField(max_length=64, blank=True, default="")
    schema_version = models.CharField(max_length=4, blank=True, default="")
    received_at = models.DateTimeField(default=timezone.now)
    occurred_at = models.DateTimeField()
    period = models.CharField(max_length=7)
    booked_period = models.CharField(max_length=7, blank=True, default="")
    rated_entry = models.ForeignKey(
        ManagedCreditEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    not_billable_reason = models.CharField(max_length=32, blank=True, default="")
    rated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~(
                    models.Q(rated_entry__isnull=False) & ~models.Q(not_billable_reason="")
                ),
                name="usage_event_record_one_outcome",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(not_billable_reason="")
                    | models.Q(not_billable_reason__in=list(NOT_BILLABLE_REASONS))
                ),
                name="usage_event_record_reason_known",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "period", "product"],
                name="uer_org_period_product_idx",
            ),
            models.Index(fields=["request_id"], name="uer_request_idx"),
            models.Index(
                fields=["rated_at", "received_at"], name="uer_rated_received_idx"
            ),
        ]


class LedgerServiceCredential(models.Model):
    """A producer's ingest credential (usage-event-v1 section 4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    token_sha256 = models.CharField(max_length=64, unique=True)
    systems = models.JSONField(default=list)  # allowed source.system values
    products = models.JSONField(default=list)  # allowed products
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({'on' if self.enabled else 'off'})"


class LedgerCredentialBinding(models.Model):
    """An org (and, for ee_daemon, a workspace) a credential may send for."""

    credential = models.ForeignKey(
        LedgerServiceCredential, on_delete=models.CASCADE, related_name="bindings"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    # Null: every workspace of the org (not allowed for ee_daemon events).
    workspace_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["credential", "organization", "workspace_id"],
                name="ledger_binding_unique",
            )
        ]


class UsageEventRefusal(models.Model):
    """One refused ingest item, kept so nothing is acknowledged and dropped."""

    id = models.BigAutoField(primary_key=True)
    received_at = models.DateTimeField(default=timezone.now)
    credential = models.ForeignKey(
        LedgerServiceCredential,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source_system = models.CharField(max_length=32, blank=True, default="")
    item_index = models.IntegerField()
    event_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=40)
    detail = models.CharField(max_length=500, blank=True, default="")
    payload_sha256 = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["reason", "-received_at"], name="uerf_reason_idx")
        ]


class StripeEventLog(models.Model):
    """One Stripe event, unique on its id (PLAN 4.3)."""

    event_id = models.CharField(max_length=255, primary_key=True)
    event_type = models.CharField(max_length=128)
    mode = models.CharField(max_length=4)
    object_id = models.CharField(max_length=255, blank=True, default="")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(max_length=16, default="received")
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    entry = models.ForeignKey(
        ManagedCreditEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=["test", "live"]),
                name="stripe_event_log_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["received", "processed", "failed", "ignored", "quarantined"]
                ),
                name="stripe_event_log_status",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "received_at"], name="sel_status_idx"),
            models.Index(fields=["object_id"], name="sel_object_idx"),
        ]


class BillingInvoiceMirror(models.Model):
    """A Stripe invoice as last synced (billing-ledger-read-v1 gap G4)."""

    id = models.BigAutoField(primary_key=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="+", db_index=False
    )
    mode = models.CharField(max_length=4)
    stripe_invoice_id = models.CharField(max_length=255)
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=16)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="usd")
    subtotal_cents = models.BigIntegerField()
    tax_cents = models.BigIntegerField(default=0)
    total_cents = models.BigIntegerField()
    amount_due_cents = models.BigIntegerField(default=0)
    hosted_invoice_url = models.CharField(max_length=2048, blank=True, default="")
    invoice_pdf_url = models.CharField(max_length=2048, blank=True, default="")
    stripe_created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mode", "stripe_invoice_id"], name="invoice_mirror_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["usage", "plan", "topup", "one_off", "legacy"]),
                name="invoice_mirror_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["draft", "open", "paid", "uncollectible", "void"]
                ),
                name="invoice_mirror_status",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_cents__gte=0) & models.Q(amount_due_cents__gte=0),
                name="invoice_mirror_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-stripe_created_at", "-stripe_invoice_id"],
                name="invoice_mirror_org_idx",
            )
        ]


class ReconciliationRun(models.Model):
    """One run of an invariant check (PLAN 4.4)."""

    id = models.BigAutoField(primary_key=True)
    check_name = models.CharField(max_length=8)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    exempt = models.BooleanField(default=False)
    checked = models.IntegerField(default=0)
    failures = models.JSONField(default=list)

    class Meta:
        indexes = [
            models.Index(fields=["check_name", "-started_at"], name="recon_check_idx")
        ]

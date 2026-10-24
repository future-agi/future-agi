"""Managed-credit ledger: the money system of record for gateway managed models.

``ManagedCreditEntry`` is append-only. Every credit and debit is a new row with
a unique ``idempotency_key``, and ``ManagedCreditAccount`` holds the running
totals the ledger service keeps in step with it. All amounts are integer
micros of USD. The gateway's Redis copy is only the hot path.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models.organization import Organization


class ImmutableLedgerEntryError(Exception):
    """A ledger entry was about to be changed or removed."""


# The only column a later write may set, once the entry's usage event is
# emitted. Every other column is fixed at insert.
MUTABLE_ENTRY_FIELDS = frozenset({"usage_event_emitted_at"})


class ManagedCreditEntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        fixed = sorted(set(kwargs) - MUTABLE_ENTRY_FIELDS)
        if fixed:
            raise ImmutableLedgerEntryError(
                f"Ledger entries are append-only; cannot update {fixed}."
            )
        return super().update(**kwargs)

    update.alters_data = True

    def delete(self):
        raise ImmutableLedgerEntryError("Ledger entries are append-only.")

    delete.alters_data = True
    delete.queryset_only = True


class ManagedCreditAccount(models.Model):
    """Running totals for one org: ``available = granted - spent - held``.

    ``version`` increases on every credit-side entry. The gateway applies a
    granted push only when its version is newer than the one it holds, and
    ``gateway_acked_version`` records the last version it accepted.
    """

    organization = models.OneToOneField(
        Organization,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="managed_credit_account",
    )
    granted_total_micros = models.BigIntegerField(default=0)
    settled_spend_micros = models.BigIntegerField(default=0)
    version = models.BigIntegerField(default=0)
    gateway_acked_version = models.BigIntegerField(default=0)

    low_balance_threshold_micros = models.BigIntegerField(null=True, blank=True)
    autoreload_enabled = models.BooleanField(default=False)
    autoreload_threshold_micros = models.BigIntegerField(null=True, blank=True)
    autoreload_amount_micros = models.BigIntegerField(null=True, blank=True)
    autoreload_payment_method_id = models.CharField(
        max_length=255, blank=True, default=""
    )
    autoreload_disabled_reason = models.CharField(
        max_length=255, blank=True, default=""
    )

    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    last_drift_micros = models.BigIntegerField(null=True, blank=True)
    last_upstream_drift_micros = models.BigIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"{self.organization_id} granted={self.granted_total_micros} "
            f"spent={self.settled_spend_micros} v{self.version}"
        )


class ManagedCreditEntry(models.Model):
    """One immutable credit or debit. Spends are negative, credits positive."""

    class Kind(models.TextChoices):
        TOPUP = "topup"
        GRANT = "grant"
        SPEND = "spend"
        REFUND = "refund"
        ADJUSTMENT = "adjustment"
        EXPIRY = "expiry"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="managed_credit_entries",
        db_index=False,  # covered by the (organization, ...) indexes below
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount_micros = models.BigIntegerField()
    idempotency_key = models.CharField(max_length=200, unique=True)

    # Spend context, copied from the managed-settlement-v1 record.
    request_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    attempt_count = models.PositiveSmallIntegerField(null=True, blank=True)
    model = models.CharField(max_length=255, blank=True, default="")
    provider = models.CharField(max_length=255, blank=True, default="")
    endpoint_id = models.CharField(max_length=255, blank=True, default="")
    served_upstream_provider = models.CharField(max_length=64, blank=True, default="")
    upstream_cost_micros = models.BigIntegerField(null=True, blank=True)
    # Upstream cost plus the upstream fee and card processing. Internal only.
    landed_cost_micros = models.BigIntegerField(null=True, blank=True)
    upstream_fee_bps = models.IntegerField(null=True, blank=True)
    processing_fee_bps = models.IntegerField(null=True, blank=True)
    upstream_key_id = models.CharField(max_length=128, blank=True, default="")
    markup_micros = models.BigIntegerField(null=True, blank=True)
    markup_bps = models.IntegerField(null=True, blank=True)
    cost_source = models.CharField(max_length=32, blank=True, default="")
    usage_estimated = models.BooleanField(default=False)
    catalog_snapshot_id = models.CharField(max_length=32, blank=True, default="")
    policy_version = models.CharField(max_length=64, blank=True, default="")
    api_key_id = models.CharField(max_length=255, blank=True, default="")
    agent_run_id = models.CharField(max_length=128, blank=True, default="")
    attempts = models.JSONField(default=list, blank=True)

    # Top-up context.
    stripe_ref = models.CharField(max_length=255, blank=True, default="")
    payment_fee_micros = models.BigIntegerField(default=0)
    payment_fee_estimated = models.BooleanField(default=False)

    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    usage_event_emitted_at = models.DateTimeField(null=True, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ManagedCreditEntryQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="spend", amount_micros__lte=0)
                    | models.Q(
                        kind__in=["topup", "grant", "refund"], amount_micros__gte=0
                    )
                    | models.Q(kind__in=["adjustment", "expiry"])
                ),
                name="managed_credit_entry_amount_sign",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at"],
                name="mc_entry_org_occurred_idx",
            ),
            models.Index(
                fields=["organization", "kind", "-occurred_at"],
                name="mc_entry_org_kind_idx",
            ),
            models.Index(
                fields=["organization", "agent_run_id"],
                name="mc_entry_org_run_idx",
            ),
        ]

    def __str__(self):
        return f"{self.idempotency_key} {self.kind} {self.amount_micros}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLedgerEntryError(
                f"Ledger entry {self.pk} is append-only; write a new entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLedgerEntryError("Ledger entries are append-only.")

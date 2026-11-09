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


# The only column a later write may set: the settlement drain (D3, not built
# yet) is to stamp it once the entry's usage event is emitted. Every other
# column is fixed at insert, except one one-way fill: a top-up credited
# without its PaymentIntent gets it once (``fill_payment_intent``).
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

    def fill_payment_intent(self, payment_intent_id: str) -> int:
        """Record ``payment_intent_id`` on top-ups credited without one.

        Ruling 492: a top-up written before its PaymentIntent was known (R's
        pre-2a ``apply_topup``) gets it when a replay names it, so that
        PaymentIntent can never credit a second time. Only a ``topup`` row
        whose id is blank changes, from blank to the id; a recorded id is never
        replaced, and no amount or bucket column moves. The partial unique
        ``mc_entry_topup_one_payment_intent`` still applies: when another
        top-up holds the id, the database refuses the write (IntegrityError).
        Returns the number of rows filled.
        """
        if not isinstance(payment_intent_id, str) or not payment_intent_id:
            raise ImmutableLedgerEntryError("only a PaymentIntent id can fill a blank one")
        return models.QuerySet.update(
            self.filter(kind="topup", stripe_payment_intent_id=""),
            stripe_payment_intent_id=payment_intent_id,
        )

    fill_payment_intent.alters_data = True

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

    # --- one billing ledger (BL-LEDGER, usage/0031) -------------------------
    # P: wallet-funded usage debits. settled_spend_micros stays gateway-only.
    platform_spend_micros = models.BigIntegerField(default=0)
    # Gross credits per funding bucket (I5: their grant sums).
    promo_total_micros = models.BigIntegerField(default=0)
    commit_total_micros = models.BigIntegerField(default=0)
    purchased_total_micros = models.BigIntegerField(default=0)
    # What is left per bucket, and the overdraft (services/funding.py).
    # balance = promo + commit + purchased - debt = F - S - P.
    promo_balance_micros = models.BigIntegerField(default=0)
    commit_balance_micros = models.BigIntegerField(default=0)
    purchased_balance_micros = models.BigIntegerField(default=0)
    debt_micros = models.BigIntegerField(default=0)
    # +1 per entry written; orders balance-cache writes.
    entry_seq = models.BigIntegerField(default=0)
    # "" until a push answers; "ok", or "na_managed_off" after a 404
    # managed_models_disabled (no retry until managed models are on).
    gateway_push_state = models.CharField(max_length=16, blank=True, default="")
    gateway_push_state_at = models.DateTimeField(null=True, blank=True)

    # PLAN 1.1 puts these on OrganizationSubscription; models/usage.py is not
    # this lane's file, so the org's one ledger account holds them. "" means
    # derived from the subscription (services/managed_credits.billing_profile).
    funding = models.CharField(max_length=24, blank=True, default="")
    billing_channel = models.CharField(max_length=24, blank=True, default="")
    billing_path = models.CharField(max_length=8, default="legacy")
    cutover_at = models.DateTimeField(null=True, blank=True)

    # Auto top-up caps and the one charge in flight (PLAN 2.3).
    autoreload_daily_count_cap = models.IntegerField(null=True, blank=True)
    autoreload_daily_amount_cap_micros = models.BigIntegerField(null=True, blank=True)
    autoreload_seq = models.BigIntegerField(default=0)
    autoreload_in_flight_seq = models.BigIntegerField(null=True, blank=True)
    autoreload_in_flight_at = models.DateTimeField(null=True, blank=True)
    autoreload_consent_version = models.CharField(max_length=64, blank=True, default="")
    autoreload_consent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(promo_balance_micros__gte=0)
                    & models.Q(commit_balance_micros__gte=0)
                    & models.Q(purchased_balance_micros__gte=0)
                    & models.Q(debt_micros__gte=0)
                ),
                name="managed_credit_account_buckets_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(billing_path__in=["legacy", "ledger"]),
                name="managed_credit_account_billing_path",
            ),
        ]

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
        # BL-LEDGER (PLAN 1.2); every name fits max_length=16.
        USAGE_DEBIT = "usage_debit"
        TOPUP_REVERSAL = "topup_reversal"
        DISPUTE_HOLD = "dispute_hold"
        DISPUTE_RELEASE = "dispute_release"

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

    # --- one billing ledger (BL-LEDGER, usage/0031); "" or null when unset ----
    product = models.CharField(max_length=16, blank=True, default="")
    dimension = models.CharField(max_length=30, blank=True, default="")
    quantity = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    unit = models.CharField(max_length=32, blank=True, default="")
    # The bucket a credit filled, or the one bucket a debit drew; "" when a
    # debit drew several (the funded_* columns hold the split).
    funding = models.CharField(max_length=16, blank=True, default="")
    period = models.CharField(max_length=7, blank=True, default="")  # YYYY-MM booked
    late_of_period = models.CharField(max_length=7, blank=True, default="")
    # A plain UUID, never a foreign key: a workspace delete must neither be
    # blocked by nor delete money rows.
    workspace_id = models.UUIDField(null=True, blank=True)
    agent_id = models.CharField(max_length=128, blank=True, default="")
    purpose = models.CharField(max_length=16, blank=True, default="")
    source_kind = models.CharField(max_length=32, blank=True, default="")
    source_ref = models.CharField(max_length=255, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    category = models.CharField(max_length=32, blank=True, default="")
    # catalog_id of the price catalog in force (price-catalog.v<N>). Not
    # catalog_snapshot_id, which keeps the gateway's routing evidence (G5).
    price_catalog_version = models.CharField(max_length=32, blank=True, default="")
    # The entry this one reverses: a refund's spend, a reversal's top-up, a
    # release's hold.
    related_entry = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    stripe_event_id = models.CharField(max_length=255, blank=True, default="")
    stripe_mode = models.CharField(max_length=4, blank=True, default="")
    # Signed per-bucket changes; their sum is amount_micros (I2, enforced by
    # the funded_sum constraint). funded_debt_micros is the change of -debt.
    # All null on rows written before usage/0031 and on invoice-funded debits.
    funded_promo_micros = models.BigIntegerField(null=True, blank=True)
    funded_commit_micros = models.BigIntegerField(null=True, blank=True)
    funded_purchased_micros = models.BigIntegerField(null=True, blank=True)
    funded_debt_micros = models.BigIntegerField(null=True, blank=True)

    objects = ManagedCreditEntryQuerySet.as_manager()

    class Meta:
        constraints = [
            # G1 (billing-ledger-read-v1 section 7): the four new kinds, with
            # every row the 0030 constraint admitted still admitted.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind__in=["spend", "usage_debit", "topup_reversal", "dispute_hold"],
                        amount_micros__lte=0,
                    )
                    | models.Q(
                        kind__in=["topup", "grant", "refund", "dispute_release"],
                        amount_micros__gte=0,
                    )
                    | models.Q(kind__in=["adjustment", "expiry"])
                ),
                name="managed_credit_entry_amount_sign",
            ),
            # I2 at write time.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        funded_promo_micros__isnull=True,
                        funded_commit_micros__isnull=True,
                        funded_purchased_micros__isnull=True,
                        funded_debt_micros__isnull=True,
                    )
                    | models.Q(
                        amount_micros=models.F("funded_promo_micros")
                        + models.F("funded_commit_micros")
                        + models.F("funded_purchased_micros")
                        + models.F("funded_debt_micros")
                    )
                ),
                name="managed_credit_entry_funded_sum",
            ),
            # One meter per model call (PLAN 1.3, UMP guard e).
            models.UniqueConstraint(
                fields=["source_kind", "source_ref"],
                condition=models.Q(source_kind="model_call"),
                name="mc_entry_one_model_call",
            ),
            # A Checkout session and its PaymentIntent never both credit.
            models.UniqueConstraint(
                fields=["stripe_payment_intent_id"],
                condition=models.Q(kind="topup") & ~models.Q(stripe_payment_intent_id=""),
                name="mc_entry_topup_one_payment_intent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "period"],
                name="mc_entry_org_period_idx",
            ),
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

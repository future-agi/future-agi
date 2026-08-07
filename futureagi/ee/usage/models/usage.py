import uuid
from decimal import Decimal

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from django.core.validators import MinValueValidator
from django.db import models
from tfc.utils.base_model import BaseModel


class SubscriptionTierChoices(models.TextChoices):
    FREE = "free", "Free"
    BUSINESS = "basic", "Basic"
    BUSINESS_YEARLY = "basic_yearly", "Basic Yearly"
    CUSTOM = "custom", "Custom"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class IntervalChoices(models.TextChoices):
    MONTH = "month", "Month"
    YEAR = "year", "Year"


# ── NEW BILLING ENUMS (Phase 1.1) ──────────────────────────────────────────


class PlanChoices(models.TextChoices):
    """Plan tiers for the new billing system.

    Customer-facing: Free and PAYG are tiers. Boost/Scale/Enterprise are add-ons on PAYG.
    Backend: all 5 stored as plan values.
    """

    FREE = "free", "Free"
    PAYG = "payg", "Pay-as-you-go"
    BOOST = "boost", "Boost"
    SCALE = "scale", "Scale"
    ENTERPRISE = "enterprise", "Enterprise"
    CUSTOM = "custom", "Custom"


class BillingMethodChoices(models.TextChoices):
    """How an organization pays."""

    CARD = "card", "Credit Card"
    INVOICE_30 = "invoice_30", "Invoice Net-30"
    INVOICE_60 = "invoice_60", "Invoice Net-60"
    WIRE = "wire", "Wire Transfer"
    AWS_MARKETPLACE = "aws_marketplace", "AWS Marketplace"
    GCP_MARKETPLACE = "gcp_marketplace", "GCP Marketplace"


class TracingBillingModeChoices(models.TextChoices):
    """How tracing data is billed for this org.

    STORAGE: count raw bytes ingested (default for all new orgs).
    EVENTS: count discrete units — traces + spans + scores (Langfuse-compatible).
    """

    STORAGE = "storage", "Storage-based (GB)"
    EVENTS = "events", "Event-based (traces + spans + scores)"


class BillingIntervalChoices(models.TextChoices):
    """Billing cycle for add-on subscriptions."""

    MONTHLY = "monthly", "Monthly"
    ANNUAL = "annual", "Annual"


class SubscriptionTier(BaseModel):
    """Defines different subscription tiers."""

    name = models.CharField(max_length=50, choices=SubscriptionTierChoices.choices)
    description = models.TextField(blank=True)
    stripe_price_id = models.CharField(max_length=100, blank=True, null=True)
    stripe_price_ids_all = models.JSONField(default=list, blank=True, null=True)
    wallet_refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount to refill the wallet every month.",
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.name

    class Meta:
        unique_together = ("name", "description")


class OrganizationStatusChoices(models.TextChoices):
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past Due"
    UNPAID = "unpaid", "Unpaid"
    CANCELED = "canceled", "Canceled"
    INACTIVE = "inactive", "Inactive"


# active, past_due, unpaid, canceled, incomplete, incomplete_expired, trialing, paused, all, or ended
class OrganizationSubscription(BaseModel):
    # STATUS_CHOICES = [
    #     ("active", "Active"),
    #     ("past_due", "Past Due"),
    #     ("canceled", "Canceled"),
    #     ("inactive", "Inactive"),
    # ]

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, unique=True
    )
    subscription_tier = models.ForeignKey(SubscriptionTier, on_delete=models.CASCADE)
    next_renewal_date = models.DateField(
        help_text="Next due date for renewal.", null=True, blank=True
    )
    subscription_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Price of the subscription.",
        blank=True,
        null=True,
    )

    subscription_future_tier = models.CharField(
        max_length=50, choices=SubscriptionTierChoices.choices, null=True, blank=True
    )
    subscription_future_start_date = models.DateField(
        help_text="Next due date for renewal.", null=True, blank=True
    )
    subscription_future_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Price of the future subscription.",
        blank=True,
        null=True,
    )

    status = models.CharField(
        max_length=50,
        choices=OrganizationStatusChoices.choices,
        default=OrganizationStatusChoices.ACTIVE,
    )
    wallet_refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount to refill the wallet every month.",
        default=20,
    )
    wallet_balance = models.DecimalField(
        max_digits=16,
        decimal_places=8,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )

    stripe_customer_id_test = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Stripe customer ID for test mode. NULL values are allowed.",
    )
    stripe_customer_id_live = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Stripe customer ID for live mode. NULL values are allowed.",
    )
    auto_recharge_enabled = models.BooleanField(default=False)
    auto_recharge_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount to refill the wallet every month.",
        blank=True,
        null=True,
    )
    auto_recharge_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Threshold to trigger auto recharge.",
        blank=True,
        null=True,
    )
    payment_method_id = models.CharField(max_length=100, blank=True, null=True)

    last_refill_date = models.DateField(null=True, blank=True)
    last_refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount of the last refill.",
        default=0,
    )

    custom_subscription_id = models.CharField(max_length=100, blank=True, null=True)

    # ── NEW FIELDS (Phase 1.2) ──────────────────────────────────────────────

    plan = models.CharField(
        max_length=20,
        choices=PlanChoices.choices,
        default=PlanChoices.FREE,
        help_text="Current billing plan. Source of truth for entitlements.",
        db_index=True,
    )
    billing_method = models.CharField(
        max_length=20,
        choices=BillingMethodChoices.choices,
        default=BillingMethodChoices.CARD,
        help_text="How this organization pays (card, invoice, wire, marketplace).",
    )
    billing_interval = models.CharField(
        max_length=10,
        choices=BillingIntervalChoices.choices,
        default=BillingIntervalChoices.MONTHLY,
        help_text="Billing cycle for add-on subscriptions (monthly or annual).",
    )
    stripe_subscription_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Stripe subscription ID for metered usage billing.",
    )
    stripe_fee_subscription_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Stripe subscription ID for platform fee (custom plans only).",
    )
    card_network_logo_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Logo URL for the default payment method's card network.",
    )
    card_last_4_digits = models.CharField(
        max_length=4,
        blank=True,
        default="",
        help_text="Last 4 digits of the default payment method.",
    )
    tracing_billing_mode = models.CharField(
        max_length=10,
        choices=TracingBillingModeChoices.choices,
        default=TracingBillingModeChoices.STORAGE,
        help_text="How tracing data is billed: storage (GB) or events (traces+spans+scores).",
    )
    billing_period_start = models.DateField(
        null=True,
        blank=True,
        help_text="Start of current billing cycle.",
    )
    billing_period_end = models.DateField(
        null=True,
        blank=True,
        help_text="End of current billing cycle.",
    )

    # ── Custom plan billing fields ─────────────────────────────────────────
    platform_fee_billing_cycle = models.PositiveIntegerField(
        default=1,
        help_text=(
            "Months between platform fee charges for custom plans. "
            "1=monthly, 3=quarterly, 6=semi-annual, 12=annual. "
            "Per-charge amount = subscription_price * billing_cycle / 12."
        ),
    )
    contract_end_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "End date of the custom plan contract. After this date, "
            "a final usage-only invoice is generated and plan reverts to free."
        ),
    )
    plan_changed_at = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Unix ts of the most recent plan change. Used by the proration "
            "helper to compute mid-month platform-fee fractions without a "
            "Stripe API round-trip. NULL for orgs that haven't flipped plans "
            "since this column shipped — proration falls back to Stripe "
            "metadata, then ``created_at``."
        ),
    )

    def __str__(self):
        return f"{self.organization.display_name if self.organization.display_name else self.organization.name} - {self.subscription_tier.name}"

    def get_subscription_name(self):
        """
        Returns the display name of the current or upcoming subscription tier.
        Falls back to the current tier if no future tier is scheduled.
        """
        tier_key = self.subscription_tier.name
        return SubscriptionTierChoices(tier_key).label

    class Meta:
        indexes = [
            models.Index(fields=["plan"], name="idx_orgsub_plan"),
            models.Index(fields=["status", "plan"], name="idx_orgsub_status_plan"),
        ]
        constraints = [
            # Conditional unique: only enforce uniqueness when value is not NULL
            # PostgreSQL: NULL != NULL, so unique=True with null=True allows multiple NULLs
            # This constraint ensures non-NULL values are unique
            models.UniqueConstraint(
                fields=["stripe_customer_id_test"],
                condition=models.Q(stripe_customer_id_test__isnull=False),
                name="uq_orgsub_stripe_test_not_null",
            ),
            models.UniqueConstraint(
                fields=["stripe_customer_id_live"],
                condition=models.Q(stripe_customer_id_live__isnull=False),
                name="uq_orgsub_stripe_live_not_null",
            ),
        ]


# Canonical definitions live in tfc.constants.api_calls (OSS).
# Re-exported here for backward compatibility within the ee module.
from tfc.constants.api_calls import APICallTypeChoices  # noqa: F401


class APICallType(BaseModel):
    """Defines each type of API call."""

    name = models.CharField(
        max_length=50, unique=True, choices=APICallTypeChoices.choices
    )
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Pricing(BaseModel):
    """Stores the price per API call type."""

    api_call_type = models.ForeignKey(APICallType, on_delete=models.CASCADE)
    price_per_call = models.DecimalField(max_digits=16, decimal_places=8)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True
    )

    class Meta:
        unique_together = ("api_call_type", "organization")

    def __str__(self):
        return f"{self.api_call_type.name} - ${self.price_per_call}"


# currently, we are putting constraints only for dataset, users, rows in
# the below choices
class ResourceTypeChoices(models.TextChoices):
    PROJECT = "project", "Project"
    DATASET = "dataset", "Dataset"
    LOGS = "logs", "Logs"
    ROWS = "rows", "Rows"
    COLUMNS = "columns", "Columns"
    USERS = "users", "Users"
    TRACES = "traces", "Traces"
    OBSERVE = "observe", "Observe"
    PROTOTYPES = "prototypes", "Prototypes"
    KNOWLEDGE_BASE = "knowledge_base", "Knowledge Base"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class ResourceType(BaseModel):
    name = models.CharField(
        max_length=50, unique=True, choices=ResourceTypeChoices.choices
    )
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class ResourceLimits(BaseModel):
    subscription_tier = models.ForeignKey(SubscriptionTier, on_delete=models.CASCADE)
    resource_type = models.ForeignKey(ResourceType, on_delete=models.CASCADE)
    has_access = models.BooleanField(default=False)
    limit = models.PositiveIntegerField(help_text="Limit for the resource")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True
    )

    class Meta:
        unique_together = ("resource_type", "organization")

    def __str__(self):
        return f"{self.subscription_tier.name} - {self.resource_type}: {self.limit}"


class RateLimit(BaseModel):
    """Defines rate limiting thresholds for each API call type and subscription tier."""

    api_call_type = models.ForeignKey(APICallType, on_delete=models.CASCADE)
    subscription_tier = models.ForeignKey(SubscriptionTier, on_delete=models.CASCADE)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True
    )
    minute_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max calls per minute"
    )
    hour_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max calls per hour"
    )
    day_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max calls per day"
    )
    month_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Max calls per month"
    )

    class Meta:
        unique_together = (
            "api_call_type",
            "subscription_tier",
            "organization",
        )  # Unique rate limit per API call type and tier

    def __str__(self):
        return f"{self.subscription_tier.name} - {self.api_call_type.name}: Rate Limits"


from tfc.constants.api_calls import APICallStatusChoices  # noqa: F401


class OrganizationBilling(BaseModel):
    """Stores billing information for each organization."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, unique=True
    )
    billing_contact_name = models.CharField(max_length=100, blank=True, null=True)
    billing_contact_email = models.EmailField(blank=True, null=True)
    company = models.CharField(max_length=100, blank=True, null=True)  # Optional
    billing_address1 = models.CharField(max_length=255, blank=True, null=True)
    billing_address2 = models.CharField(
        max_length=255, blank=True, null=True
    )  # Optional
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    tax_id = models.CharField(max_length=50, blank=True, null=True)  # Optional

    def __str__(self):
        return f"{self.organization.display_name if self.organization.display_name else self.organization.name} - Billing Info"


class APICallLog(BaseModel):
    """Logs each API call, tracking usage and costs for each organization."""

    log_id = models.UUIDField(
        default=uuid.uuid4, null=False, editable=False, unique=True
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    api_call_type = models.ForeignKey(
        APICallType, on_delete=models.CASCADE, blank=True, null=True
    )
    cost = models.DecimalField(max_digits=16, decimal_places=8)
    deducted_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    status = models.CharField(
        max_length=50,
        choices=APICallStatusChoices.choices,
        default=APICallStatusChoices.NOT_STARTED,
    )
    refund_parent_id = models.CharField(max_length=100, null=True, blank=True)
    reference_id = models.CharField(max_length=100, null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)
    input_token_count = models.PositiveIntegerField(default=0, null=True)
    source = models.CharField(max_length=100, null=True, blank=True)
    source_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "source_id", "-created_at"],
                name="idx_apicalllog_org_source",
            ),
        ]

    def __str__(self):
        return f"{self.organization.display_name if self.organization.display_name else self.organization.name} - {self.api_call_type.name} - Cost: ${self.cost}"


# ── NEW MODELS (Phase 1.3) ─────────────────────────────────────────────────


class CreditBalance(BaseModel):
    """Promotional and prepaid credits. Applied automatically before charging card.

    Credit types:
    - startup: Startup program credits (e.g., $50K YC program)
    - referral: Referral program credits
    - goodwill: Customer support goodwill credits
    - prepaid: Migrated from legacy wallet balance or purchased credits
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="credit_balances",
        help_text="Organization this credit belongs to.",
    )
    credit_type = models.CharField(
        max_length=30,
        choices=[
            ("startup", "Startup Program"),
            ("referral", "Referral"),
            ("goodwill", "Goodwill"),
            ("prepaid", "Prepaid"),
        ],
        help_text="Type of credit.",
    )
    original_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total credit amount when issued.",
    )
    remaining_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Remaining credit balance. Decremented when applied to invoices.",
    )
    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code.",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this credit expires. NULL = never expires.",
    )
    description = models.CharField(
        max_length=200,
        help_text="Human-readable description (e.g., 'Startup program — $50K cloud credits').",
    )
    issued_by = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Admin or system that issued this credit.",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "credit_type"],
                name="idx_credit_org_type",
            ),
            models.Index(
                fields=["organization", "-expires_at"],
                name="idx_credit_org_exp",
            ),
        ]
        constraints = []

    def __str__(self):
        return (
            f"{self.organization} — {self.credit_type}: "
            f"${self.remaining_amount} remaining of ${self.original_amount}"
        )


# ── PHASE 2 MODELS ─────────────────────────────────────────────────────────


class UsageEventLog(models.Model):
    """Append-only billing audit trail.

    Every usage event from the Redis Stream gets persisted here by the consumer.
    High volume (potentially millions/day). Uses BigAutoField PK for fast inserts.
    NOT a BaseModel — no soft-delete, no updated_at. Append-only.
    """

    id = models.BigAutoField(primary_key=True)
    event_id = models.UUIDField(
        unique=True,
        help_text="Idempotency key from UsageEvent.event_id. Consumer deduplicates on this.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        db_index=True,
        help_text="Organization this event belongs to.",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional workspace context.",
    )
    event_type = models.CharField(
        max_length=50,
        help_text="API call type key from billing.yaml (e.g., 'turing_large_evaluator').",
    )
    dimension = models.CharField(
        max_length=30,
        help_text="Resolved billing dimension (e.g., 'ai_credits', 'storage').",
    )
    amount_raw = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        help_text="Units consumed in native units (bytes, credits, requests).",
    )
    amount_display = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        help_text="Units consumed in display units (GB, credits).",
    )
    period = models.CharField(
        max_length=7,
        help_text="Billing period in YYYY-MM format.",
    )
    timestamp = models.DateTimeField(
        help_text="When the usage occurred (from the event, not insert time).",
    )
    properties = models.JSONField(
        default=dict,
        help_text="Flat k/v context: source, source_id, model, workspace_id, etc.",
    )
    status = models.CharField(
        max_length=20,
        default="success",
        help_text="success, rate_limited, quota_exceeded.",
    )
    cost_estimate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Estimated cost at time of event. NULL for free-tier events.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this row was inserted by the consumer.",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "period", "dimension"],
                name="idx_uel_org_period_dim",
            ),
            models.Index(
                fields=["organization", "event_type", "-timestamp"],
                name="idx_uel_org_type_ts",
            ),
            models.Index(
                fields=["organization", "-timestamp"],
                name="idx_uel_org_ts",
            ),
            models.Index(
                fields=["period", "dimension"],
                name="idx_uel_period_dim",
            ),
        ]

    def __str__(self):
        return f"{self.organization_id} {self.event_type} {self.amount_raw} ({self.dimension})"


class UsageSummary(BaseModel):
    """Durable snapshot of Redis usage counters.

    One row per organization × dimension × billing period.
    Flushed from Redis every 5 minutes by the sync task.
    Source for Stripe reporting and the usage dashboard.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        help_text="Organization this summary belongs to.",
    )
    dimension = models.CharField(
        max_length=30,
        help_text="Billing dimension key (e.g., 'storage', 'ai_credits').",
    )
    period = models.CharField(
        max_length=7,
        help_text="Billing period in YYYY-MM format.",
    )
    total_usage = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        help_text="Cumulative usage in display units (GB, credits).",
    )
    total_usage_raw = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=0,
        help_text="Cumulative usage in native units (bytes, tokens) for precision.",
    )
    reported_to_stripe = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        default=0,
        help_text="Last value reported to Stripe. Used for delta reporting.",
    )
    last_flushed_at = models.DateTimeField(
        help_text="When this row was last updated from Redis.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "dimension", "period"],
                name="uq_usage_summary_org_dim_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "period"],
                name="idx_usagesummary_org_period",
            ),
        ]

    def __str__(self):
        return (
            f"{self.organization_id} {self.dimension} {self.period}: {self.total_usage}"
        )


# ── PHASE 3 MODELS ─────────────────────────────────────────────────────────


class PlanEntitlement(BaseModel):
    """Unified plan configuration: limits, features, allowances, rate limits, retention.

    One table for ALL plan-level config. Any value can be overridden per-org.
    Seeded from billing.yaml via sync_billing_config command.
    Per-org overrides created in Django Admin.

    Lookup order:
    1. Per-org override (organization IS NOT NULL)
    2. Plan default (organization IS NULL)
    3. billing.yaml fallback
    """

    feature = models.CharField(
        max_length=60,
        help_text=(
            "Feature key. Convention: 'monitors', 'has_knowledge_base', "
            "'free_storage_gb', 'retention_traces_days', 'api_rate_rpm'"
        ),
    )
    plan = models.CharField(
        max_length=20,
        choices=PlanChoices.choices,
        help_text="Which plan this value applies to.",
    )
    value_int = models.IntegerField(
        null=True,
        blank=True,
        help_text="Numeric value: limit count, GB, days, rpm. -1 = unlimited. NULL if boolean.",
    )
    value_bool = models.BooleanField(
        null=True,
        blank=True,
        help_text="Boolean value: feature on/off. NULL if numeric.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="NULL = plan default. Non-NULL = per-org override.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["feature", "plan", "organization"],
                name="uq_entitlement_feature_plan_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["plan", "feature"],
                name="idx_ent_plan_feature",
            ),
            models.Index(
                fields=["organization", "feature"],
                name="idx_ent_org_feature",
            ),
        ]

    def __str__(self):
        val = self.value_int if self.value_int is not None else self.value_bool
        org_label = f" [{self.organization}]" if self.organization else ""
        return f"{self.feature} / {self.plan}: {val}{org_label}"


# ── PHASE 4 MODELS ─────────────────────────────────────────────────────────


class PlanPricing(BaseModel):
    """Tiered pricing per billing dimension in display units.

    Same tiers for all plans. Per-org overrides for custom enterprise deals.
    Free allowances are in PlanEntitlement (not duplicated here).
    Seeded from billing.yaml via sync_billing_config.
    """

    dimension = models.CharField(
        max_length=30,
        help_text="Billing dimension key (e.g., 'storage', 'ai_credits').",
    )
    tier_start = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        help_text="Start of range in display units (50 for 50 GB, 2000 for 2000 credits).",
    )
    tier_end = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="End of range in display units. NULL = unlimited (last tier).",
    )
    price_per_unit = models.DecimalField(
        max_digits=16,
        decimal_places=8,
        help_text="Price per display unit ($2.00 per GB, $0.000015 per request).",
    )
    display_unit = models.CharField(
        max_length=20,
        help_text="Display unit label: GB, credits, requests, hits, tokens, minutes, events.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="NULL = default for everyone. Non-NULL = custom deal for this org.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dimension", "tier_start", "organization"],
                name="uq_pricing_dim_start_org",
            ),
        ]
        indexes = [
            models.Index(fields=["dimension"], name="idx_pricing_dim"),
            models.Index(fields=["organization"], name="idx_pricing_org"),
        ]

    def __str__(self):
        end = f"-{self.tier_end}" if self.tier_end else "+"
        org = f" [{self.organization}]" if self.organization else ""
        return f"{self.dimension}: {self.tier_start}{end} {self.display_unit} @ ${self.price_per_unit}{org}"


class Invoice(BaseModel):
    """Monthly invoice for an organization."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        help_text="Organization this invoice belongs to.",
    )
    period_start = models.DateField(help_text="Start of billing period.")
    period_end = models.DateField(help_text="End of billing period.")
    plan = models.CharField(
        max_length=20,
        help_text="Plan at time of invoice generation.",
    )
    platform_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Add-on subscription fee ($0, $250, $750, $2000).",
    )
    usage_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Sum of all usage line items.",
    )
    credits_applied = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Sum of all credit line items (positive number, subtracted from total).",
    )
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="platform_fee + usage_total - credits_applied.",
    )
    tax = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Tax amount (calculated by Stripe Tax).",
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="subtotal + tax. Final amount to charge.",
    )
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(
        max_length=20,
        default="draft",
        help_text="draft, finalized, paid, failed, void.",
    )
    stripe_invoice_id = models.CharField(max_length=100, null=True, blank=True)
    stripe_invoice_url = models.URLField(null=True, blank=True)
    stripe_pdf_url = models.URLField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "period_start"],
                name="uq_invoice_org_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-period_start"],
                name="idx_invoice_org_period",
            ),
            models.Index(fields=["status"], name="idx_invoice_status"),
        ]

    def __str__(self):
        return (
            f"Invoice {self.id} — {self.organization} {self.period_start} ${self.total}"
        )


class InvoiceLineItem(BaseModel):
    """Line item on an invoice."""

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    line_type = models.CharField(
        max_length=20,
        help_text="platform_fee, usage, credit, discount, adhoc.",
    )
    dimension = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        help_text="Billing dimension. NULL for non-usage lines.",
    )
    credit = models.ForeignKey(
        CreditBalance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Which credit was applied (for credit line items).",
    )
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        help_text="Usage amount in display units.",
    )
    unit = models.CharField(max_length=20, help_text="GB, credits, requests, etc.")
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text="Effective price per display unit.",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Line total. Negative for credits.",
    )
    tier_breakdown = models.JSONField(
        null=True,
        blank=True,
        help_text='[{"range": "0-500GB", "rate": "$2.00", "usage": 450, "cost": "$900"}]',
    )

    def __str__(self):
        return f"{self.line_type}: {self.description} = ${self.amount}"


class UsageBudget(BaseModel):
    """User-defined budget rules: per-dimension or total spend limits with actions.

    Actions: notify (email/slack), warn (notify + in-app banner), pause (block usage).
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="usage_budgets",
    )
    name = models.CharField(max_length=100, help_text="User-facing label.")
    scope = models.CharField(
        max_length=30,
        help_text="Dimension key (e.g., 'ai_credits') or 'total_spend'.",
    )
    threshold_value = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        help_text="In display units (credits, GB) or cents for total_spend.",
    )
    action = models.CharField(
        max_length=20,
        help_text="notify = email/slack only, warn = notify + banner, pause = block usage.",
    )
    notify_emails = models.JSONField(
        default=list,
        blank=True,
        help_text="Email addresses to notify. Empty = org admins.",
    )
    notify_slack_webhook = models.URLField(
        null=True,
        blank=True,
        help_text="Slack webhook URL for notifications.",
    )
    is_active = models.BooleanField(default=True)
    last_triggered_period = models.CharField(
        max_length=7,
        null=True,
        blank=True,
        help_text="YYYY-MM of last trigger (dedup: don't re-fire in same period).",
    )
    last_triggered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the budget was last triggered.",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "scope", "is_active"],
                name="idx_budget_org_scope",
            ),
        ]

    def __str__(self):
        return f"{self.organization} — {self.name}: {self.action} at {self.threshold_value}"


# ── STRIPE PRODUCT MAP (Phase 5.1) ───────────────────────────────────────────


class StripeProductMapKey(models.TextChoices):
    """Valid keys for Stripe product/price mappings.

    6 recurring add-on prices (plan × billing interval)
    + 7 metered usage prices (one per billing dimension).
    """

    # Recurring add-on prices
    BOOST_MONTHLY = "boost_monthly", "Boost Monthly"
    BOOST_ANNUAL = "boost_annual", "Boost Annual"
    SCALE_MONTHLY = "scale_monthly", "Scale Monthly"
    SCALE_ANNUAL = "scale_annual", "Scale Annual"
    ENTERPRISE_MONTHLY = "enterprise_monthly", "Enterprise Monthly"
    ENTERPRISE_ANNUAL = "enterprise_annual", "Enterprise Annual"

    # Metered usage prices (one per billing dimension)
    STORAGE = "storage", "Storage (GB)"
    AI_CREDITS = "ai_credits", "AI Credits"
    GATEWAY_REQUESTS = "gateway_requests", "Gateway Requests"
    GATEWAY_CACHE_HITS = "gateway_cache_hits", "Gateway Cache Hits"
    TEXT_SIM_TOKENS = "text_sim_tokens", "Text Sim Tokens"
    VOICE_SIM_MINUTES = "voice_sim_minutes", "Voice Sim Minutes"
    TRACING_EVENTS = "tracing_events", "Tracing Events"


class StripeProductMap(BaseModel):
    """Maps billing keys to Stripe product/price IDs.

    Stores Stripe Price IDs in the database so they can be changed
    dynamically via Django Admin without redeployment.

    13 rows total: 6 recurring (plan add-ons) + 7 metered (usage dimensions).
    Created by the `create_stripe_products` management command.
    """

    key = models.CharField(
        max_length=50,
        choices=StripeProductMapKey.choices,
        unique=True,
        db_index=True,
        help_text="Billing key — maps to a plan add-on or usage dimension.",
    )
    stripe_product_id = models.CharField(
        max_length=100,
        help_text="Stripe Product ID (prod_xxx).",
    )
    stripe_price_id = models.CharField(
        max_length=100,
        help_text="Stripe Price ID (price_xxx).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive entries are ignored during lookups.",
    )

    class Meta:
        verbose_name = "Stripe Product Map"
        verbose_name_plural = "Stripe Product Maps"

    def __str__(self):
        return f"{self.get_key_display()} → {self.stripe_price_id}"

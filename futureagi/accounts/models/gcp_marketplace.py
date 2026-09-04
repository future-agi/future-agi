import uuid

from django.db import models

from accounts.models.organization import Organization
from tfc.utils.base_model import BaseModel


class GCPMarketplaceAccountState(models.TextChoices):
    UNSPECIFIED = "ACCOUNT_STATE_UNSPECIFIED", "Unspecified"
    ACTIVATION_REQUESTED = "ACCOUNT_ACTIVATION_REQUESTED", "Activation Requested"
    ACTIVE = "ACCOUNT_ACTIVE", "Active"


class GCPMarketplaceEntitlementState(models.TextChoices):
    UNSPECIFIED = "ENTITLEMENT_STATE_UNSPECIFIED", "Unspecified"
    ACTIVATION_REQUESTED = "ENTITLEMENT_ACTIVATION_REQUESTED", "Activation Requested"
    ACTIVE = "ENTITLEMENT_ACTIVE", "Active"
    PENDING_PLAN_CHANGE_APPROVAL = (
        "ENTITLEMENT_PENDING_PLAN_CHANGE_APPROVAL",
        "Pending Plan Change Approval",
    )
    PENDING_PLAN_CHANGE = "ENTITLEMENT_PENDING_PLAN_CHANGE", "Pending Plan Change"
    PENDING_CANCELLATION = "ENTITLEMENT_PENDING_CANCELLATION", "Pending Cancellation"
    CANCELLED = "ENTITLEMENT_CANCELLED", "Cancelled"
    SUSPENDED = "ENTITLEMENT_SUSPENDED", "Suspended"


class GCPUsageReportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    REPORTED = "reported", "Reported"
    FAILED = "failed", "Failed"


class GCPMarketplaceAccount(BaseModel):
    """A Cloud Marketplace customer, linked to a Future AGI organization.

    One account per customer per provider. Created when the sign-up token is
    verified, which happens before any entitlement is known.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    procurement_account_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Bare account id, the last segment of the Google resource name.",
    )
    google_user_identity = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Obfuscated Google user id from the sign-up token. Supplied once, at sign-up, and never retrievable afterwards.",
    )
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gcp_marketplace_account",
    )
    state = models.CharField(
        max_length=64,
        choices=GCPMarketplaceAccountState.choices,
        default=GCPMarketplaceAccountState.UNSPECIFIED,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "gcp_marketplace_account"
        verbose_name = "GCP Marketplace Account"
        verbose_name_plural = "GCP Marketplace Accounts"

    def __str__(self):
        return self.procurement_account_id


class GCPMarketplaceEntitlement(BaseModel):
    """One purchase of one plan. An account may hold several."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    entitlement_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Bare entitlement id, the key every Pub/Sub event carries.",
    )
    account = models.ForeignKey(
        GCPMarketplaceAccount,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="entitlements",
        help_text="Null while an entitlement event arrives before sign-up completes.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gcp_marketplace_entitlements",
    )

    plan_id = models.CharField(max_length=255, blank=True)
    new_pending_plan = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=64,
        choices=GCPMarketplaceEntitlementState.choices,
        default=GCPMarketplaceEntitlementState.UNSPECIFIED,
        db_index=True,
    )

    usage_reporting_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="consumerId for Service Control. Usage cannot be reported without it.",
    )

    effective_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    offer = models.CharField(max_length=512, blank=True)
    offer_end_time = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    google_update_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Entitlement updateTime. Discard events older than the stored value.",
    )
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "gcp_marketplace_entitlement"
        verbose_name = "GCP Marketplace Entitlement"
        verbose_name_plural = "GCP Marketplace Entitlements"

    def __str__(self):
        return f"{self.entitlement_id} ({self.plan_id})"


class GCPMarketplaceUsageCheckpoint(BaseModel):
    """One usage report of one metric for one window.

    Unique on (entitlement, metric, window_start) so a replay cannot double-report.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="gcp_marketplace_usage_checkpoints",
    )
    entitlement = models.ForeignKey(
        GCPMarketplaceEntitlement,
        on_delete=models.CASCADE,
        related_name="usage_checkpoints",
    )
    metric = models.CharField(max_length=255)
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    quantity_reported = models.DecimalField(max_digits=24, decimal_places=6)
    operation_id = models.CharField(
        max_length=255,
        help_text="Id sent with the report. Google documents no dedupe on it, so this table is what prevents a resend.",
    )
    reported_at = models.DateTimeField(null=True, blank=True)
    report_status = models.CharField(
        max_length=20,
        choices=GCPUsageReportStatus.choices,
        default=GCPUsageReportStatus.PENDING,
        db_index=True,
    )
    error_detail = models.TextField(blank=True)

    class Meta:
        db_table = "gcp_marketplace_usage_checkpoint"
        verbose_name = "GCP Marketplace Usage Checkpoint"
        verbose_name_plural = "GCP Marketplace Usage Checkpoints"
        constraints = [
            models.UniqueConstraint(
                fields=["entitlement", "metric", "window_start"],
                name="uq_gcp_mkt_usage_entitlement_metric_window",
            )
        ]

    def __str__(self):
        return f"{self.entitlement_id}:{self.metric}:{self.window_start:%Y-%m}"


class GCPMarketplaceProcessedEvent(BaseModel):
    """Pub/Sub dedupe ledger.

    Delivery is at-least-once and the ack deadline can expire mid-handler, so two
    workers can process the same event concurrently. The unique constraint, not
    application logic, is what makes handling once-only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=64, blank=True)
    subject_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Entitlement or account id the event is about, taken from the message body.",
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "gcp_marketplace_processed_event"
        verbose_name = "GCP Marketplace Processed Event"
        verbose_name_plural = "GCP Marketplace Processed Events"

    def __str__(self):
        return f"{self.event_type}:{self.event_id}"

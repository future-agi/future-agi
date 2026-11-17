from django.db import models

from accounts.models import Organization


class AgentccRequestAttempt(models.Model):
    """One upstream attempt of a gateway request.

    Written by log ingestion from ``log-entry-v2.attempts[]``. A request has at
    most 16 attempts. Rows are kept by their own age, because the log row
    they point to can be purged first (the FK is SET_NULL).
    """

    class Kind(models.TextChoices):
        PRIMARY = "primary"
        RETRY = "retry"
        FAILOVER = "failover"
        MODEL_FALLBACK = "model_fallback"
        AUTO_FALLBACK = "auto_fallback"
        REQUEST_FALLBACK = "request_fallback"
        HEDGE = "hedge"
        SELECTOR = "selector"

    class Outcome(models.TextChoices):
        SUCCESS = "success"
        ERROR = "error"
        TIMEOUT = "timeout"
        CANCELLED = "cancelled"
        DISCARDED = "discarded"
        SKIPPED = "skipped"
        CIRCUIT_OPEN = "circuit_open"
        # A 200 with no usable content.
        HOLLOW = "hollow"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_request_attempts",
        db_index=False,  # covered by the (organization, ...) indexes below
    )
    request_log = models.ForeignKey(
        "prism.AgentccRequestLog",
        on_delete=models.SET_NULL,
        related_name="attempts",
        null=True,
        blank=True,
    )
    request_id = models.CharField(max_length=255)
    # "<request_id>.<index>"
    attempt_id = models.CharField(max_length=300, unique=True)
    index = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=32, choices=Kind.choices)
    reason = models.CharField(max_length=64, blank=True, default="")

    provider = models.CharField(max_length=255, blank=True, default="")
    model = models.CharField(max_length=255, blank=True, default="")
    endpoint_id = models.CharField(max_length=255, blank=True, default="")
    upstream_provider = models.CharField(max_length=255, blank=True, default="")
    served_upstream_provider = models.CharField(max_length=64, blank=True, default="")
    # key_hash of the org's managed upstream key; never the key itself.
    upstream_key_id = models.CharField(max_length=128, blank=True, default="")
    credential_type = models.CharField(max_length=8, blank=True, default="")

    status_code = models.IntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    error_class = models.CharField(max_length=32, blank=True, default="")
    error_origin = models.CharField(max_length=16, blank=True, default="")
    error_message = models.CharField(max_length=512, blank=True, default="")

    started_at = models.DateTimeField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    ttfb_ms = models.IntegerField(null=True, blank=True)

    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    cached_tokens = models.IntegerField(null=True, blank=True)
    cache_write_tokens = models.IntegerField(null=True, blank=True)
    reasoning_tokens = models.IntegerField(null=True, blank=True)

    upstream_cost_micros = models.BigIntegerField(null=True, blank=True)
    billed_cost_micros = models.BigIntegerField(null=True, blank=True)
    landed_cost_micros = models.BigIntegerField(null=True, blank=True)
    upstream_fee_bps = models.IntegerField(null=True, blank=True)
    processing_fee_bps = models.IntegerField(null=True, blank=True)
    cost_source = models.CharField(max_length=32, blank=True, default="")
    usage_estimated = models.BooleanField(default=False)
    billable = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_request_attempt"
        indexes = [
            models.Index(
                fields=["organization", "request_id"],
                name="agentcc_att_org_request_idx",
            ),
            # Retention purges attempts by their own age.
            models.Index(
                fields=["organization", "created_at"],
                name="agentcc_att_org_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.attempt_id} {self.kind} {self.outcome}"

"""Configuration tables: endpoints, their hot state rows, secrets, subscriptions and the org/workspace policy
(DESIGN.md sections 4.1-4.4 and 4.9)."""

from __future__ import annotations

from django.db import models

from webhooks.models.ids import (
    ID,
    new_endpoint_id,
    new_policy_id,
    new_secret_id,
    new_subscription_id,
)


class Endpoint(models.Model):
    # A null workspace means org-wide here: never let FA's thread-local auto-assignment (tfc/utils/signals.py) fill it
    # with the request's workspace (WH-2 found it turning org-wide endpoints and org policies into workspace ones).
    disable_auto_workspace_assignment = True

    KIND = [
        ("http", "http"),
        ("github_dispatch", "github_dispatch"),
        ("cli_listen", "cli_listen"),
    ]
    STATUS = [
        ("enabled", "enabled"),
        ("disabled_manual", "disabled_manual"),
        ("disabled_auto", "disabled_auto"),
        ("blocked_by_policy", "blocked_by_policy"),
    ]
    MODE = [("thin", "thin"), ("full", "full")]

    id = models.CharField(default=new_endpoint_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    kind = models.CharField(max_length=20, choices=KIND, default="http")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    url_encrypted = models.BinaryField(null=True)
    url_host = models.CharField(max_length=253, blank=True, default="")
    url_display = models.TextField(blank=True, default="")
    url_sha256 = models.CharField(max_length=64, blank=True, default="")
    github_repository = models.CharField(max_length=200, blank=True, default="")
    github_event_type = models.CharField(max_length=100, blank=True, default="")
    github_token_encrypted = models.BinaryField(null=True)
    listen_session_id = models.CharField(max_length=64, blank=True, default="")
    listen_expires_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, choices=STATUS, default="enabled")
    disabled_reason = models.CharField(max_length=30, blank=True, default="")
    disabled_at = models.DateTimeField(null=True)
    disabled_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    headers_encrypted = models.BinaryField(null=True)
    header_names = models.JSONField(default=list)
    payload_mode = models.CharField(max_length=10, choices=MODE, default="thin")
    include_people_names = models.BooleanField(default=True)
    accepts_run_targeting = models.BooleanField(default=False)
    api_version = models.CharField(max_length=10, default="v1")
    timeout_ms = models.PositiveIntegerField(default=15_000)
    rate_limit_per_s = models.PositiveIntegerField(default=10)
    max_in_flight = models.PositiveIntegerField(default=4)
    max_age_s = models.PositiveIntegerField(default=259_200)
    legacy_signing_key_encrypted = models.BinaryField(null=True)
    legacy_signing_until = models.DateTimeField(null=True)
    owner = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "webhooks_endpoint"
        indexes = [
            models.Index(
                fields=["organization", "deleted", "status"], name="whep_org_live"
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(timeout_ms__gte=5000, timeout_ms__lte=30000),
                name="whep_timeout_range",
            ),
            models.CheckConstraint(
                condition=models.Q(rate_limit_per_s__gte=1, rate_limit_per_s__lte=100),
                name="whep_rate_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_in_flight__gte=1, max_in_flight__lte=16),
                name="whep_inflight_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_age_s__gte=3600, max_age_s__lte=259200),
                name="whep_max_age_range",
            ),
            # A unique name per scope among live endpoints (section 4.1). Two partial indexes, because NULL
            # workspaces (org-wide endpoints) are distinct in a plain unique index.
            models.UniqueConstraint(
                fields=["organization", "workspace", "name"],
                condition=models.Q(deleted=False, workspace__isnull=False),
                name="whep_live_name_ws",
            ),
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False, workspace__isnull=True),
                name="whep_live_name_org",
            ),
        ]


class EndpointState(models.Model):
    """The hot row, one per endpoint (R6, R9): sequence, send rate, in-flight, lane and health."""

    endpoint = models.OneToOneField(
        Endpoint, primary_key=True, on_delete=models.CASCADE, related_name="state"
    )
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    next_seq = models.BigIntegerField(default=1)
    rate_halved_until = models.DateTimeField(null=True)
    in_flight = models.IntegerField(default=0)
    lane = models.CharField(max_length=10, default="normal")
    slow_until = models.DateTimeField(null=True)
    consecutive_failed_attempts = models.IntegerField(default=0)
    consecutive_ssrf_blocked = models.IntegerField(default=0)
    failing_since = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    last_failure_at = models.DateTimeField(null=True)
    last_status_code = models.IntegerField(null=True)
    last_outcome = models.CharField(max_length=30, blank=True, default="")
    recent_latency_ms = models.JSONField(default=list)
    recent_timeouts_at = models.JSONField(default=list)
    paused_count = models.IntegerField(default=0)
    last_failing_notice_at = models.DateTimeField(null=True)

    # ---- WH-1b operations (migration 0002_operations) ----------------------------------------------------------
    rate_tat = models.DateTimeField(null=True)
    """The rate's theoretical arrival time: the earliest start of the next send to this endpoint, on the database
    clock, shared by every loop and replica (``webhooks.core.ratelimit``). It replaces WH-1a's ``tokens`` and
    ``tokens_at``, which 0002_operations drops."""
    last_disabled_notice_at = models.DateTimeField(null=True)
    """When the "turned off" email for the current turn-off went out; null until it has (section 13.3)."""

    class Meta:
        db_table = "webhooks_endpoint_state"


class Secret(models.Model):
    STATE = [("active", "active"), ("previous", "previous"), ("revoked", "revoked")]

    id = models.CharField(default=new_secret_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    endpoint = models.ForeignKey(
        Endpoint, on_delete=models.CASCADE, related_name="secrets"
    )
    secret_encrypted = models.BinaryField(null=True)
    tail = models.CharField(max_length=4)
    state = models.CharField(max_length=10, choices=STATE, default="active")
    expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "webhooks_secret"
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint"],
                condition=models.Q(state="active"),
                name="whk_one_active",
            ),
            models.UniqueConstraint(
                fields=["endpoint"],
                condition=models.Q(state="previous"),
                name="whk_one_previous",
            ),
        ]


class Subscription(models.Model):
    id = models.CharField(default=new_subscription_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    endpoint = models.ForeignKey(
        Endpoint, on_delete=models.CASCADE, related_name="subscriptions"
    )
    event_type = models.CharField(max_length=100)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "webhooks_subscription"
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint", "event_type"], name="whsub_unique_type"
            )
        ]


class Policy(models.Model):
    # A null workspace means org-wide here: never let FA's thread-local auto-assignment (tfc/utils/signals.py) fill it
    # with the request's workspace (WH-2 found it turning org-wide endpoints and org policies into workspace ones).
    disable_auto_workspace_assignment = True

    id = models.CharField(default=new_policy_id, **ID)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace", null=True, on_delete=models.CASCADE, related_name="+"
    )
    allowed_hosts = models.JSONField(default=list)
    members_can_manage = models.BooleanField(default=False)
    full_payload_allowed = models.BooleanField(default=False)
    max_endpoints_per_workspace = models.IntegerField(default=50)
    log_retention_days = models.IntegerField(default=90)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "webhooks_policy"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(workspace__isnull=True),
                name="whpol_one_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "workspace"], name="whpol_one_ws"
            ),
        ]

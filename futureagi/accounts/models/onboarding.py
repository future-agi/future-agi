import uuid

from django.db import models
from django.utils import timezone

from tfc.utils.base_model import BaseModel


class OnboardingActivationEvent(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_activation_events",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="onboarding_activation_events",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_activation_events",
    )
    event_name = models.CharField(max_length=96, db_index=True)
    product_path = models.CharField(max_length=32, blank=True, default="")
    activation_stage = models.CharField(max_length=96, blank=True, default="")
    source = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    is_sample = models.BooleanField(default=False, db_index=True)
    idempotency_key = models.CharField(
        max_length=160,
        null=True,
        blank=True,
        db_index=True,
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "accounts_onboarding_activation_event"
        ordering = ("-occurred_at", "-created_at")
        indexes = [
            models.Index(
                fields=["organization", "workspace", "event_name", "-occurred_at"],
                name="onb_evt_org_ws_name_ts",
            ),
            models.Index(
                fields=["organization", "workspace", "product_path", "-occurred_at"],
                name="onb_evt_org_ws_path_ts",
            ),
            models.Index(
                fields=["user", "event_name", "-occurred_at"],
                name="onb_evt_user_name_ts",
            ),
            models.Index(
                fields=["workspace", "is_sample", "event_name"],
                name="onb_evt_ws_sample_name",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False, deleted=False),
                name="onb_evt_unique_idempotency",
            )
        ]

    def __str__(self):
        return f"{self.event_name} for {self.workspace_id} at {self.occurred_at}"


class OnboardingPaidCloudActivationExportLog(BaseModel):
    DEPLOYMENT_MODE_CLOUD = "cloud"
    DEPLOYMENT_MODE_EE = "ee"
    DEPLOYMENT_MODE_OSS = "oss"

    DEPLOYMENT_MODE_CHOICES = (
        (DEPLOYMENT_MODE_CLOUD, DEPLOYMENT_MODE_CLOUD),
        (DEPLOYMENT_MODE_EE, DEPLOYMENT_MODE_EE),
        (DEPLOYMENT_MODE_OSS, DEPLOYMENT_MODE_OSS),
    )

    STATUS_READY = "ready"
    STATUS_SUPPRESSED = "suppressed"
    STATUS_EXPORTED = "exported"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = (
        (STATUS_READY, STATUS_READY),
        (STATUS_SUPPRESSED, STATUS_SUPPRESSED),
        (STATUS_EXPORTED, STATUS_EXPORTED),
        (STATUS_FAILED, STATUS_FAILED),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_paid_cloud_activation_export_logs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="onboarding_paid_cloud_activation_export_logs",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_paid_cloud_activation_export_logs",
    )
    deployment_mode = models.CharField(
        max_length=16,
        choices=DEPLOYMENT_MODE_CHOICES,
    )
    run_id = models.UUIDField(db_index=True)
    region = models.CharField(max_length=16, blank=True, default="")
    plan_tier = models.CharField(max_length=64, blank=True, default="")
    schema_version = models.CharField(max_length=96)
    event_cursor = models.CharField(max_length=160)
    idempotency_key = models.CharField(max_length=220, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_READY,
        db_index=True,
    )
    suppression_reason = models.CharField(max_length=64, null=True, blank=True)
    fact_payload = models.JSONField(default=dict, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True, db_index=True)
    evaluated_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_paid_cloud_activation_export_log"
        ordering = ("-evaluated_at", "-created_at")
        indexes = [
            models.Index(
                fields=["status", "evaluated_at"],
                name="onb_paid_exp_status_eval",
            ),
            models.Index(
                fields=["run_id", "status"],
                name="onb_paid_exp_run_status",
            ),
            models.Index(
                fields=["workspace", "status", "-evaluated_at"],
                name="onb_paid_exp_ws_status",
            ),
            models.Index(
                fields=["workspace", "event_cursor"],
                name="onb_paid_exp_ws_cursor",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "idempotency_key"],
                condition=models.Q(deleted=False),
                name="onb_paid_exp_unique_idemp",
            )
        ]

    def __str__(self):
        return f"{self.event_cursor}:{self.status} for {self.workspace_id}"


class OnboardingActivationFactReceipt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    export_log_id = models.UUIDField(db_index=True)
    idempotency_key = models.CharField(max_length=220, db_index=True)
    schema_version = models.CharField(max_length=96, db_index=True)
    event_cursor = models.CharField(max_length=160, blank=True, default="")
    organization_id_value = models.UUIDField(db_index=True)
    workspace_id_value = models.UUIDField(db_index=True)
    user_id_value = models.UUIDField(null=True, blank=True, db_index=True)
    deployment_mode = models.CharField(max_length=16, blank=True, default="")
    deployment_region = models.CharField(
        max_length=16,
        blank=True,
        default="",
        db_index=True,
    )
    plan_tier = models.CharField(max_length=64, blank=True, default="", db_index=True)
    activation_stage = models.CharField(
        max_length=96,
        blank=True,
        default="",
        db_index=True,
    )
    primary_path = models.CharField(
        max_length=32, blank=True, default="", db_index=True
    )
    is_activated = models.BooleanField(default=False, db_index=True)
    lifecycle_campaign_key = models.CharField(
        max_length=96,
        blank=True,
        default="",
        db_index=True,
    )
    lifecycle_template_key = models.CharField(max_length=96, blank=True, default="")
    lifecycle_status = models.CharField(max_length=64, blank=True, default="")
    email_next_key = models.CharField(max_length=96, blank=True, default="")
    email_eligible = models.BooleanField(default=False, db_index=True)
    email_suppressed = models.BooleanField(default=False, db_index=True)
    journey_config_schema_version = models.CharField(
        max_length=96,
        blank=True,
        default="",
    )
    primary_cohort_key = models.CharField(
        max_length=96,
        blank=True,
        default="",
        db_index=True,
    )
    cohort_keys = models.JSONField(default=list, blank=True)
    journey_cohorts = models.JSONField(default=list, blank=True)
    payload_hash = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    evaluated_at = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_activation_fact_receipt"
        ordering = ("-received_at", "-created_at")
        indexes = [
            models.Index(
                fields=["workspace_id_value", "-evaluated_at"],
                name="onb_fact_ws_eval",
            ),
            models.Index(
                fields=["user_id_value", "-evaluated_at"],
                name="onb_fact_user_eval",
            ),
            models.Index(
                fields=["activation_stage", "primary_path"],
                name="onb_fact_stage_path",
            ),
            models.Index(
                fields=["primary_cohort_key", "-evaluated_at"],
                name="onb_fact_cohort_eval",
            ),
            models.Index(
                fields=["lifecycle_campaign_key", "-evaluated_at"],
                name="onb_fact_campaign_eval",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(deleted=False),
                name="onb_fact_unique_idemp",
            )
        ]

    def __str__(self):
        return f"{self.idempotency_key}:{self.activation_stage}"


class OnboardingActivationFactReceiptRejection(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reason = models.CharField(max_length=96, db_index=True)
    message = models.CharField(max_length=240, blank=True, default="")
    export_log_id = models.UUIDField(null=True, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=220, blank=True, default="")
    schema_version = models.CharField(max_length=96, blank=True, default="")
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_activation_fact_receipt_rejection"
        ordering = ("-received_at", "-created_at")
        indexes = [
            models.Index(
                fields=["reason", "-received_at"],
                name="onb_fact_rej_reason_ts",
            ),
            models.Index(
                fields=["idempotency_key", "-received_at"],
                name="onb_fact_rej_idemp_ts",
            ),
        ]

    def __str__(self):
        return f"{self.reason}:{self.idempotency_key or self.export_log_id}"


class OnboardingGoal(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_goals",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="onboarding_goals",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="selected_onboarding_goals",
    )
    goal = models.CharField(max_length=64)
    primary_path = models.CharField(max_length=32)
    source = models.CharField(max_length=64, blank=True, default="")
    reason = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    selected_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "accounts_onboarding_goal"
        ordering = ("-selected_at", "-created_at")
        indexes = [
            models.Index(
                fields=["organization", "workspace", "is_active"],
                name="onb_goal_org_ws_active",
            ),
            models.Index(
                fields=["organization", "workspace", "primary_path"],
                name="onb_goal_org_ws_path",
            ),
            models.Index(
                fields=["user", "-selected_at"],
                name="onb_goal_user_selected",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace"],
                condition=models.Q(is_active=True, deleted=False),
                name="onb_goal_unique_active_workspace",
            )
        ]

    def __str__(self):
        return f"{self.goal} for {self.workspace_id}"


class OnboardingSampleProject(BaseModel):
    STATUS_NOT_CREATED = "not_created"
    STATUS_CREATING = "creating"
    STATUS_READY_FOR_OBSERVE = "ready_for_observe"
    STATUS_PARTIALLY_READY = "partially_ready"
    STATUS_READY = "ready"
    STATUS_HIDDEN = "hidden"
    STATUS_UNAVAILABLE = "unavailable"
    STATUS_REPAIR_FAILED = "repair_failed"

    STATUS_CHOICES = (
        (STATUS_NOT_CREATED, STATUS_NOT_CREATED),
        (STATUS_CREATING, STATUS_CREATING),
        (STATUS_READY_FOR_OBSERVE, STATUS_READY_FOR_OBSERVE),
        (STATUS_PARTIALLY_READY, STATUS_PARTIALLY_READY),
        (STATUS_READY, STATUS_READY),
        (STATUS_HIDDEN, STATUS_HIDDEN),
        (STATUS_UNAVAILABLE, STATUS_UNAVAILABLE),
        (STATUS_REPAIR_FAILED, STATUS_REPAIR_FAILED),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_sample_projects",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="onboarding_sample_projects",
    )
    first_opened_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="first_opened_onboarding_sample_projects",
    )
    last_opened_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_opened_onboarding_sample_projects",
    )
    manifest_id = models.CharField(max_length=96)
    manifest_version = models.CharField(max_length=32)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_NOT_CREATED,
        db_index=True,
    )
    artifact_refs = models.JSONField(default=dict, blank=True)
    missing_artifacts = models.JSONField(default=list, blank=True)
    health = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=180, db_index=True)
    repair_attempts = models.PositiveIntegerField(default=0)
    last_repair_attempt_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    hidden_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_sample_project"
        ordering = ("-last_opened_at", "-updated_at")
        indexes = [
            models.Index(
                fields=["organization", "workspace", "manifest_id"],
                name="onb_sample_org_ws_manifest",
            ),
            models.Index(fields=["workspace", "status"], name="onb_sample_ws_status"),
            models.Index(
                fields=["workspace", "hidden_at"], name="onb_sample_ws_hidden"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "manifest_id", "manifest_version"],
                condition=models.Q(deleted=False),
                name="onb_sample_unique_manifest",
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(deleted=False),
                name="onb_sample_unique_idempotency",
            ),
        ]

    def __str__(self):
        return f"{self.manifest_id}:{self.manifest_version} for {self.workspace_id}"


class OnboardingLifecyclePreference(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_preferences",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_preferences",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="onboarding_lifecycle_preferences",
    )
    onboarding_enabled = models.BooleanField(default=True)
    first_action_recovery_enabled = models.BooleanField(default=True)
    sample_bridge_enabled = models.BooleanField(default=True)
    next_loop_enabled = models.BooleanField(default=True)
    daily_digest_enabled = models.BooleanField(default=False)
    reactivation_enabled = models.BooleanField(default=False)
    snoozed_until = models.DateTimeField(null=True, blank=True, db_index=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_lifecycle_preference"
        ordering = ("-updated_at", "-created_at")
        indexes = [
            models.Index(
                fields=["user", "organization"],
                name="onb_life_pref_user_org",
            ),
            models.Index(
                fields=["user", "organization", "workspace"],
                name="onb_life_pref_user_org_ws",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "workspace"],
                condition=models.Q(workspace__isnull=False, deleted=False),
                name="onb_life_pref_unique_ws",
            ),
            models.UniqueConstraint(
                fields=["user", "organization"],
                condition=models.Q(workspace__isnull=True, deleted=False),
                name="onb_life_pref_unique_user",
            ),
        ]

    def __str__(self):
        scope = self.workspace_id or "user"
        return f"onboarding lifecycle preference {self.user_id}:{scope}"


class OnboardingLifecycleEvaluationLog(BaseModel):
    STATUS_ELIGIBLE = "eligible"
    STATUS_SUPPRESSED = "suppressed"
    STATUS_SKIPPED = "skipped"
    STATUS_ERROR = "error"

    STATUS_CHOICES = (
        (STATUS_ELIGIBLE, STATUS_ELIGIBLE),
        (STATUS_SUPPRESSED, STATUS_SUPPRESSED),
        (STATUS_SKIPPED, STATUS_SKIPPED),
        (STATUS_ERROR, STATUS_ERROR),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_id = models.UUIDField(db_index=True)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_evaluations",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_evaluations",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_evaluations",
    )
    campaign_key = models.CharField(max_length=96, null=True, blank=True)
    campaign_group = models.CharField(max_length=64, null=True, blank=True)
    template_key = models.CharField(max_length=96, null=True, blank=True)
    template_version = models.CharField(max_length=32, null=True, blank=True)
    activation_stage = models.CharField(max_length=96)
    primary_path = models.CharField(max_length=32, null=True, blank=True)
    recommendation_id = models.CharField(max_length=96, null=True, blank=True)
    target_action_id = models.CharField(max_length=96, null=True, blank=True)
    target_success_event = models.CharField(max_length=96, null=True, blank=True)
    target_url = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, db_index=True)
    suppression_reason = models.CharField(max_length=64, null=True, blank=True)
    suppression_details = models.JSONField(default=dict, blank=True)
    eligible_at = models.DateTimeField(null=True, blank=True)
    evaluated_at = models.DateTimeField(default=timezone.now, db_index=True)
    activation_state_snapshot = models.JSONField(default=dict, blank=True)
    registry_snapshot = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    source_receipt = models.OneToOneField(
        "accounts.OnboardingActivationFactReceipt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lifecycle_evaluation",
    )

    class Meta:
        db_table = "accounts_onboarding_lifecycle_evaluation_log"
        ordering = ("-evaluated_at", "-created_at")
        indexes = [
            models.Index(fields=["user", "-evaluated_at"], name="onb_life_user_ts"),
            models.Index(
                fields=["workspace", "-evaluated_at"],
                name="onb_life_ws_ts",
            ),
            models.Index(
                fields=["workspace", "campaign_key", "-evaluated_at"],
                name="onb_life_ws_campaign_ts",
            ),
            models.Index(
                fields=["workspace", "status", "-evaluated_at"],
                name="onb_life_ws_status_ts",
            ),
            models.Index(
                fields=["campaign_key", "status", "-evaluated_at"],
                name="onb_life_campaign_status",
            ),
            models.Index(
                fields=["suppression_reason", "-evaluated_at"],
                name="onb_life_reason_ts",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["run_id", "user", "workspace", "campaign_key"],
                name="onb_life_unique_run_campaign",
            )
        ]

    def __str__(self):
        campaign = self.campaign_key or "none"
        return f"{campaign}:{self.status} for {self.workspace_id}"


class OnboardingLifecycleSendAllowlist(BaseModel):
    SCOPE_USER = "user"
    SCOPE_WORKSPACE = "workspace"
    SCOPE_ORGANIZATION = "organization"
    SCOPE_DOMAIN = "domain"

    SCOPE_CHOICES = (
        (SCOPE_USER, SCOPE_USER),
        (SCOPE_WORKSPACE, SCOPE_WORKSPACE),
        (SCOPE_ORGANIZATION, SCOPE_ORGANIZATION),
        (SCOPE_DOMAIN, SCOPE_DOMAIN),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_type = models.CharField(max_length=32, choices=SCOPE_CHOICES)
    scope_value = models.CharField(max_length=255)
    campaign_group = models.CharField(max_length=64, null=True, blank=True)
    environment = models.CharField(max_length=32, default="local", db_index=True)
    enabled = models.BooleanField(default=True, db_index=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_onboarding_lifecycle_send_allowlists",
    )

    class Meta:
        db_table = "accounts_onboarding_lifecycle_send_allowlist"
        ordering = ("scope_type", "scope_value", "campaign_group")
        indexes = [
            models.Index(
                fields=["environment", "enabled", "scope_type"],
                name="onb_send_allow_env_scope",
            ),
            models.Index(
                fields=["scope_type", "scope_value"],
                name="onb_send_allow_scope_value",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "scope_type",
                    "scope_value",
                    "campaign_group",
                    "environment",
                ],
                condition=models.Q(campaign_group__isnull=False, deleted=False),
                name="onb_send_allow_unique_group",
            ),
            models.UniqueConstraint(
                fields=["scope_type", "scope_value", "environment"],
                condition=models.Q(campaign_group__isnull=True, deleted=False),
                name="onb_send_allow_unique_scope",
            ),
        ]

    def __str__(self):
        group = self.campaign_group or "all"
        return f"{self.environment}:{self.scope_type}:{self.scope_value}:{group}"


class OnboardingLifecycleSendLog(BaseModel):
    STATUS_QUEUED = "queued"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CLICKED = "clicked"
    STATUS_COMPLETED = "completed"
    STATUS_SUPPRESSED = "suppressed"

    STATUS_CHOICES = (
        (STATUS_QUEUED, STATUS_QUEUED),
        (STATUS_SENT, STATUS_SENT),
        (STATUS_FAILED, STATUS_FAILED),
        (STATUS_CLICKED, STATUS_CLICKED),
        (STATUS_COMPLETED, STATUS_COMPLETED),
        (STATUS_SUPPRESSED, STATUS_SUPPRESSED),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation_log = models.ForeignKey(
        "accounts.OnboardingLifecycleEvaluationLog",
        on_delete=models.CASCADE,
        related_name="send_logs",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_send_logs",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_lifecycle_send_logs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="onboarding_lifecycle_send_logs",
    )
    campaign_key = models.CharField(max_length=96, db_index=True)
    campaign_group = models.CharField(max_length=64, db_index=True)
    template_key = models.CharField(max_length=96)
    template_version = models.CharField(max_length=32)
    primary_path = models.CharField(max_length=32, null=True, blank=True)
    activation_stage = models.CharField(max_length=96)
    recommended_action_id = models.CharField(max_length=96, null=True, blank=True)
    target_success_event = models.CharField(max_length=96, null=True, blank=True)
    target_route = models.TextField()
    click_url = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    suppression_reason = models.CharField(max_length=64, null=True, blank=True)
    provider_status = models.CharField(max_length=32, null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    clicked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_lifecycle_send_log"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "-created_at"], name="onb_send_user_ts"),
            models.Index(
                fields=["workspace", "campaign_key", "-created_at"],
                name="onb_send_ws_campaign_ts",
            ),
            models.Index(
                fields=["campaign_key", "status", "-created_at"],
                name="onb_send_campaign_status",
            ),
            models.Index(
                fields=["target_success_event", "status"],
                name="onb_send_target_status",
            ),
            models.Index(fields=["sent_at"], name="onb_send_sent_at"),
            models.Index(fields=["clicked_at"], name="onb_send_clicked_at"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation_log", "campaign_key", "user", "workspace"],
                condition=models.Q(deleted=False),
                name="onb_send_unique_eval",
            )
        ]

    def __str__(self):
        return f"{self.campaign_key}:{self.status} for {self.user_id}"


class OnboardingQualityAction(BaseModel):
    STATUS_OPEN = "open"
    STATUS_COMPLETED = "completed"
    STATUS_DISMISSED = "dismissed"

    STATUS_CHOICES = (
        (STATUS_OPEN, STATUS_OPEN),
        (STATUS_COMPLETED, STATUS_COMPLETED),
        (STATUS_DISMISSED, STATUS_DISMISSED),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="onboarding_quality_actions",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="onboarding_quality_actions",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_onboarding_quality_actions",
    )
    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_onboarding_quality_actions",
    )
    product_path = models.CharField(max_length=32, db_index=True)
    action_key = models.CharField(max_length=160)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
    )
    label = models.CharField(max_length=180)
    body = models.CharField(max_length=300, blank=True, default="")
    route = models.TextField(blank=True, default="/dashboard/home")
    fallback_route = models.TextField(blank=True, default="/dashboard/get-started")
    source_type = models.CharField(max_length=64, blank=True, default="workspace")
    source_id = models.CharField(max_length=128, blank=True, default="")
    is_sample = models.BooleanField(default=False, db_index=True)
    opened_at = models.DateTimeField(null=True, blank=True, db_index=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dismissed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_event_at = models.DateTimeField(db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_onboarding_quality_action"
        ordering = ("-last_event_at", "-created_at")
        indexes = [
            models.Index(
                fields=["organization", "workspace", "product_path", "status"],
                name="onb_qact_org_ws_path_status",
            ),
            models.Index(
                fields=["workspace", "status", "-last_event_at"],
                name="onb_qact_ws_status_ts",
            ),
            models.Index(
                fields=["workspace", "source_type", "source_id"],
                name="onb_qact_ws_source",
            ),
            models.Index(
                fields=["assigned_to", "status", "-last_event_at"],
                name="onb_qact_assignee_status",
            ),
            models.Index(
                fields=["workspace", "status", "due_at"],
                name="onb_qact_ws_status_due",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "product_path", "action_key"],
                condition=models.Q(deleted=False),
                name="onb_qact_unique_action",
            )
        ]

    def __str__(self):
        return f"{self.action_key}:{self.status} for {self.workspace_id}"


class NotificationPreference(BaseModel):
    FAMILY_PRODUCT_ONBOARDING = "product_onboarding"
    FAMILY_DAILY_QUALITY_DIGEST = "daily_quality_digest"
    FAMILY_USAGE_BUDGET = "usage_budget"
    FAMILY_GATEWAY_ALERT = "gateway_alert"
    FAMILY_OBSERVE_MONITOR = "observe_monitor"
    FAMILY_EVAL_QUALITY_ALERT = "eval_quality_alert"
    FAMILY_WORKSPACE_ADMIN = "workspace_admin"

    CHANNEL_EMAIL = "email"
    CHANNEL_IN_APP = "in_app"
    CHANNEL_SLACK = "slack"
    CHANNEL_WEBHOOK = "webhook"

    FAMILY_CHOICES = (
        (FAMILY_PRODUCT_ONBOARDING, FAMILY_PRODUCT_ONBOARDING),
        (FAMILY_DAILY_QUALITY_DIGEST, FAMILY_DAILY_QUALITY_DIGEST),
        (FAMILY_USAGE_BUDGET, FAMILY_USAGE_BUDGET),
        (FAMILY_GATEWAY_ALERT, FAMILY_GATEWAY_ALERT),
        (FAMILY_OBSERVE_MONITOR, FAMILY_OBSERVE_MONITOR),
        (FAMILY_EVAL_QUALITY_ALERT, FAMILY_EVAL_QUALITY_ALERT),
        (FAMILY_WORKSPACE_ADMIN, FAMILY_WORKSPACE_ADMIN),
    )
    CHANNEL_CHOICES = (
        (CHANNEL_EMAIL, CHANNEL_EMAIL),
        (CHANNEL_IN_APP, CHANNEL_IN_APP),
        (CHANNEL_SLACK, CHANNEL_SLACK),
        (CHANNEL_WEBHOOK, CHANNEL_WEBHOOK),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_preferences",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_preferences",
    )
    family = models.CharField(max_length=64, choices=FAMILY_CHOICES, db_index=True)
    channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES, db_index=True)
    enabled = models.BooleanField(default=True, db_index=True)
    mute_until = models.DateTimeField(null=True, blank=True, db_index=True)
    frequency_cap_minutes = models.PositiveIntegerField(null=True, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_notification_preferences",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_notification_preferences",
    )

    class Meta:
        db_table = "accounts_notification_preference"
        ordering = ("family", "channel", "-updated_at")
        indexes = [
            models.Index(
                fields=["organization", "workspace", "family", "channel"],
                name="notif_pref_org_ws_family",
            ),
            models.Index(
                fields=["organization", "user", "family", "channel"],
                name="notif_pref_org_user_family",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "family", "channel"],
                condition=models.Q(
                    workspace__isnull=True,
                    user__isnull=True,
                    deleted=False,
                ),
                name="notif_pref_unique_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "workspace", "family", "channel"],
                condition=models.Q(
                    workspace__isnull=False,
                    user__isnull=True,
                    deleted=False,
                ),
                name="notif_pref_unique_ws",
            ),
            models.UniqueConstraint(
                fields=["organization", "user", "family", "channel"],
                condition=models.Q(
                    workspace__isnull=True,
                    user__isnull=False,
                    deleted=False,
                ),
                name="notif_pref_unique_user_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "workspace", "user", "family", "channel"],
                condition=models.Q(
                    workspace__isnull=False,
                    user__isnull=False,
                    deleted=False,
                ),
                name="notif_pref_unique_user_ws",
            ),
        ]

    def __str__(self):
        scope = self.workspace_id or self.user_id or "organization"
        return f"{self.family}:{self.channel}:{scope}"


class NotificationChannel(BaseModel):
    TYPE_EMAIL_LIST = "email_list"
    TYPE_SLACK_WEBHOOK = "slack_webhook"
    TYPE_WEBHOOK = "webhook"

    STATUS_UNTESTED = "untested"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"

    TYPE_CHOICES = (
        (TYPE_EMAIL_LIST, TYPE_EMAIL_LIST),
        (TYPE_SLACK_WEBHOOK, TYPE_SLACK_WEBHOOK),
        (TYPE_WEBHOOK, TYPE_WEBHOOK),
    )
    STATUS_CHOICES = (
        (STATUS_UNTESTED, STATUS_UNTESTED),
        (STATUS_READY, STATUS_READY),
        (STATUS_FAILED, STATUS_FAILED),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="notification_channels",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_channels",
    )
    type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    display_name = models.CharField(max_length=120)
    target_identifier = models.CharField(max_length=255, blank=True, default="")
    encrypted_config = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_notification_channels",
    )
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_UNTESTED,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_notification_channel"
        ordering = ("type", "display_name")
        indexes = [
            models.Index(
                fields=["organization", "workspace", "type", "is_active"],
                name="notif_channel_org_ws_type",
            ),
            models.Index(
                fields=["organization", "is_active"],
                name="notif_channel_org_active",
            ),
        ]

    def __str__(self):
        return f"{self.type}:{self.display_name}"


class NotificationDeliveryLog(BaseModel):
    STATUS_ELIGIBLE = "eligible"
    STATUS_SUPPRESSED = "suppressed"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CLICKED = "clicked"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = (
        (STATUS_ELIGIBLE, STATUS_ELIGIBLE),
        (STATUS_SUPPRESSED, STATUS_SUPPRESSED),
        (STATUS_SENT, STATUS_SENT),
        (STATUS_FAILED, STATUS_FAILED),
        (STATUS_CLICKED, STATUS_CLICKED),
        (STATUS_COMPLETED, STATUS_COMPLETED),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="notification_delivery_logs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_delivery_logs",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_delivery_logs",
    )
    family = models.CharField(max_length=64, db_index=True)
    source_type = models.CharField(max_length=64)
    source_id = models.CharField(max_length=128, null=True, blank=True)
    channel = models.CharField(max_length=32, db_index=True)
    recipient_type = models.CharField(max_length=64, blank=True, default="")
    recipient_identifier_masked = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    notification_key = models.CharField(max_length=160, blank=True, default="")
    idempotency_key = models.CharField(
        max_length=220,
        null=True,
        blank=True,
        db_index=True,
    )
    stage = models.CharField(max_length=96, blank=True, default="")
    severity = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, db_index=True)
    suppressed_reason = models.CharField(max_length=64, null=True, blank=True)
    route_url = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "accounts_notification_delivery_log"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=["organization", "workspace", "family", "-created_at"],
                name="notif_log_org_ws_family",
            ),
            models.Index(
                fields=["organization", "status", "-created_at"],
                name="notif_log_org_status",
            ),
            models.Index(
                fields=["family", "channel", "status"],
                name="notif_log_family_channel",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False, deleted=False),
                name="notif_log_unique_idempotency",
            )
        ]

    def __str__(self):
        return f"{self.family}:{self.channel}:{self.status}"

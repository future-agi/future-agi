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

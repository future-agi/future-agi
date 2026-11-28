"""Context operational records; published bytes stay on the HubSkill backbone."""

import uuid

from django.conf import settings
from django.db import models

from model_hub.models.skills_hub_v11 import ScopedRecord


class ContextRecord(ScopedRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class HubSkillContextDependency(ContextRecord):
    parent_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.CASCADE,
        related_name="hub_skill_context_dependencies",
    )
    child_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_context_parents",
    )
    child_skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.PROTECT,
        related_name="hub_skill_context_dependents",
    )
    mount = models.CharField(max_length=255)
    requested_selector = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent_version", "mount"], name="hub_ctx_dependency_mount"
            ),
            models.UniqueConstraint(
                fields=["parent_version", "child_skill"],
                name="hub_ctx_dependency_child",
            ),
        ]


class HubSkillContextOperation(ContextRecord):
    principal_key = models.CharField(max_length=100)
    operation = models.CharField(max_length=64)
    idempotency_key = models.UUIDField()
    request_digest = models.CharField(max_length=71)
    result_resource_id = models.UUIDField(null=True)
    response = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "workspace",
                    "principal_key",
                    "operation",
                    "idempotency_key",
                ],
                name="hub_ctx_operation_key",
            )
        ]


class HubSkillContextCredential(ContextRecord):
    class Purpose(models.TextChoices):
        CONSUME = "consume"
        REPORT = "report"

    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    secret_hash = models.CharField(max_length=64)
    grants = models.JSONField(default=list)
    binding_ids = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_context_credentials",
    )
    last_used_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "workspace", "-created_at"],
                name="hub_ctx_credential_scope",
            )
        ]


class HubSkillContextResolution(ContextRecord):
    credential = models.ForeignKey(
        HubSkillContextCredential,
        on_delete=models.CASCADE,
        related_name="hub_skill_resolutions",
    )
    version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.CASCADE,
        related_name="hub_skill_resolutions",
    )
    resource_kind = models.CharField(max_length=16)
    digest = models.CharField(max_length=71)
    requested = models.JSONField()
    grant = models.JSONField()
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(
                fields=["credential", "expires_at"], name="hub_ctx_resolution_expiry"
            )
        ]


class HubSkillContextBinding(ContextRecord):
    context = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_bindings",
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(
        max_length=16, choices=[("repository", "repository"), ("runtime", "runtime")]
    )
    selector = models.JSONField()
    update_mode = models.CharField(max_length=24)
    profile = models.CharField(max_length=64)
    profile_version = models.CharField(max_length=64)
    repository = models.JSONField(null=True)
    configuration = models.JSONField(default=dict)
    configuration_digest = models.CharField(max_length=71, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    expected_report_interval_seconds = models.PositiveIntegerField()
    retired_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class HubSkillContextSession(ContextRecord):
    binding = models.ForeignKey(
        HubSkillContextBinding,
        on_delete=models.CASCADE,
        related_name="hub_skill_sessions",
    )
    client_session_key = models.CharField(max_length=200)
    incarnation_id = models.UUIDField()
    version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_sessions",
    )
    digest = models.CharField(max_length=71)
    binding_revision = models.PositiveBigIntegerField()
    label_event = models.ForeignKey(
        "model_hub.HubSkillLabelEvent",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_sessions",
    )
    credential = models.ForeignKey(
        HubSkillContextCredential,
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_sessions",
    )
    adapter_version = models.CharField(max_length=100)
    runtime_version = models.CharField(max_length=100)
    runtime_config_digest = models.CharField(max_length=71)
    last_seen_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["binding", "client_session_key", "incarnation_id"],
                name="hub_ctx_session_identity",
            )
        ]


class HubSkillContextReceipt(ContextRecord):
    binding = models.ForeignKey(
        HubSkillContextBinding,
        on_delete=models.CASCADE,
        related_name="hub_skill_receipts",
    )
    session = models.ForeignKey(
        HubSkillContextSession,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_receipts",
    )
    reporter_key = models.CharField(max_length=100)
    event_id = models.UUIDField()
    instance_id = models.UUIDField()
    sequence = models.PositiveBigIntegerField()
    stage = models.CharField(max_length=32)
    version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_receipts",
    )
    digest = models.CharField(max_length=71)
    materialization_digest = models.CharField(max_length=71, null=True)
    request_digest = models.CharField(max_length=71)
    observed_at = models.DateTimeField()
    details = models.JSONField(default=dict)
    origin = models.CharField(max_length=24)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "reporter_key", "event_id"],
                name="hub_ctx_receipt_event",
            ),
            models.UniqueConstraint(
                fields=["binding", "instance_id", "sequence"],
                name="hub_ctx_receipt_sequence",
            ),
        ]


class HubSkillContextQuarantine(ContextRecord):
    version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.CASCADE,
        related_name="hub_skill_quarantine_events",
    )
    revision = models.PositiveBigIntegerField()
    active = models.BooleanField()
    reason = models.CharField(max_length=2000)
    replacement_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_quarantine_replacements",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_quarantine_events",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "revision"], name="hub_ctx_quarantine_revision"
            )
        ]
        indexes = [
            models.Index(
                fields=["version", "-revision"], name="hub_ctx_quarantine_current"
            )
        ]


class HubSkillContextCapture(ContextRecord):
    context = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_captures",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_captures",
    )
    trace_ref = models.JSONField()
    source_fingerprint = models.CharField(max_length=71)
    source_hashes = models.JSONField(default=dict)
    selected_text = models.JSONField(null=True)
    recorded_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_captures",
    )
    expires_at = models.DateTimeField()
    accepted_receipt = models.JSONField(null=True)


class HubSkillContextCampaign(ContextRecord):
    name = models.CharField(max_length=120)
    revision = models.PositiveBigIntegerField(default=1)
    target_ids = models.JSONField(default=list)
    status = models.CharField(max_length=16, default="draft")
    manifest_digest = models.CharField(max_length=71, null=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_skill_campaigns_authored",
    )
    authorizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_campaigns_authorized",
    )
    authorized_at = models.DateTimeField(null=True)
    parent_campaign = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_reverse_campaigns",
    )
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        app_label = "model_hub"
        indexes = [
            models.Index(
                fields=["organization", "workspace", "-created_at"],
                name="hub_ctx_campaign_scope",
            )
        ]


class HubSkillContextCampaignTarget(ContextRecord):
    campaign = models.ForeignKey(
        HubSkillContextCampaign,
        on_delete=models.CASCADE,
        related_name="hub_skill_targets",
    )
    binding = models.ForeignKey(
        "model_hub.HubSkillContextBinding",
        on_delete=models.PROTECT,
        related_name="hub_skill_campaign_targets",
    )
    binding_revision = models.PositiveBigIntegerField()
    desired_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_campaign_targets",
    )
    plan = models.JSONField(null=True)
    plan_digest = models.CharField(max_length=71, null=True)
    decision = models.CharField(max_length=16, default="pending")
    reason = models.CharField(max_length=500, blank=True)
    attempts = models.JSONField(default=list)

    class Meta:
        app_label = "model_hub"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "binding"], name="hub_ctx_campaign_binding"
            )
        ]

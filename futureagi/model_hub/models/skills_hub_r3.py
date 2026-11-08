"""Retained review/intake/native contracts. No alternative content or job executor."""

from django.conf import settings
from django.db import models

from model_hub.models.skills_hub_v12 import ContextRecord


class HubSkillDraftCheckpoint(ContextRecord):
    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_checkpoints",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_checkpoints",
    )
    draft_revision = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=32)
    payload = models.JSONField(null=True)
    digest = models.CharField(max_length=71)
    expires_at = models.DateTimeField()
    restored_revision = models.PositiveBigIntegerField(null=True)


class HubSkillIntakePlan(ContextRecord):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_intake_plans",
    )
    kind = models.CharField(max_length=16)
    fingerprint = models.CharField(max_length=71)
    inventory = models.JSONField()
    payload = models.JSONField(null=True)
    expires_at = models.DateTimeField()


class HubSkillIntakeReceipt(ContextRecord):
    plan = models.ForeignKey(
        HubSkillIntakePlan,
        on_delete=models.PROTECT,
        related_name="hub_skill_item_receipts",
    )
    item_key = models.CharField(max_length=255)
    operation = models.OneToOneField(
        "model_hub.HubSkillContextOperation",
        on_delete=models.PROTECT,
        related_name="hub_skill_intake_receipt",
    )
    destination = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.PROTECT,
        related_name="hub_skill_intake_receipts",
    )
    result = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "item_key"], name="hub_skill_intake_item"
            )
        ]


class HubSkillMigrationReceipt(ContextRecord):
    schema = models.PositiveSmallIntegerField(default=1)
    source_origin = models.CharField(max_length=255)
    source_project = models.CharField(max_length=200)
    source_resource = models.CharField(max_length=200)
    source_revision = models.CharField(max_length=200)
    source_digest = models.CharField(max_length=71)
    destination = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.PROTECT,
        related_name="hub_skill_migration_receipts",
    )
    destination_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_migration_receipts",
    )
    destination_digest = models.CharField(max_length=71)
    relation = models.CharField(
        max_length=16, choices=[("identical", "identical"), ("derived", "derived")]
    )
    mapping = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "workspace",
                    "source_origin",
                    "source_project",
                    "source_resource",
                    "source_revision",
                    "destination",
                ],
                name="hub_skill_migration_identity",
            )
        ]


class HubSkillReleaseRequest(ContextRecord):
    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_release_requests",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_skill_release_requests",
    )
    revision = models.PositiveBigIntegerField(default=1)
    status = models.CharField(max_length=24, default="pending")
    updated_at = models.DateTimeField(auto_now=True)


class HubSkillReleasePacket(ContextRecord):
    request = models.ForeignKey(
        HubSkillReleaseRequest,
        on_delete=models.CASCADE,
        related_name="hub_skill_packets",
    )
    revision = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=71)
    candidate = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_release_packets",
    )
    packet = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "revision"], name="hub_skill_packet_revision"
            )
        ]


class HubSkillReleaseRecipient(ContextRecord):
    request = models.ForeignKey(
        HubSkillReleaseRequest,
        on_delete=models.CASCADE,
        related_name="hub_skill_recipients",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_review_inbox",
    )
    last_seen_revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "recipient"], name="hub_skill_review_recipient"
            )
        ]


class HubSkillReleaseDecision(ContextRecord):
    packet = models.ForeignKey(
        HubSkillReleasePacket,
        on_delete=models.PROTECT,
        related_name="hub_skill_decisions",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_skill_release_decisions",
    )
    decision = models.CharField(
        max_length=24,
        choices=[("approve", "approve"), ("request_changes", "request_changes")],
    )
    comment = models.CharField(max_length=2000, blank=True)
    label_event = models.ForeignKey(
        "model_hub.HubSkillLabelEvent",
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_decisions",
    )
    currentness_at_action = models.JSONField(null=True)
    post_move_state = models.JSONField(null=True)


class HubSkillEvidenceAnnotation(ContextRecord):
    source = models.CharField(max_length=24)
    source_id = models.UUIDField()
    revision = models.PositiveBigIntegerField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_skill_evidence_annotations",
    )
    check_id = models.UUIDField(null=True)
    attempt_id = models.UUIDField(null=True)
    verdict = models.CharField(
        max_length=16,
        choices=[
            ("agree", "agree"),
            ("disagree", "disagree"),
            ("cannot_assess", "cannot_assess"),
        ],
    )
    cited_text = models.CharField(max_length=2000, blank=True)
    comment = models.CharField(max_length=2000, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "source", "source_id", "revision"],
                name="hub_skill_annotation_revision",
            )
        ]


class HubSkillNativeEvidence(ContextRecord):
    """Private ownership link, not a second Simulation job or public artifact URL."""

    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_native_evidence",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_native_evidence",
    )
    simulation_job = models.OneToOneField(
        "simulate.HostedHarnessJob",
        on_delete=models.PROTECT,
        related_name="hub_skill_native_evidence",
    )
    visibility = models.CharField(max_length=16, default="private")
    shared_at = models.DateTimeField(null=True)
    candidate_digest = models.CharField(max_length=71)
    execution_fingerprint = models.CharField(max_length=71)
    configuration = models.JSONField()
    planned_matrix = models.JSONField()
    annotation_revision = models.PositiveBigIntegerField(default=0)
    payload_expires_at = models.DateTimeField()
    payload_purged_at = models.DateTimeField(null=True)
    protected_locations = models.JSONField(default=list)


class HubSkillSpendPolicy(ContextRecord):
    revision = models.PositiveBigIntegerField(default=1)
    # No invented paid allowance: an administrator must supply an explicit policy.
    period_limit_usd = models.DecimalField(max_digits=18, decimal_places=8)
    enabled = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hub_skill_spend_policies",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace"], name="hub_skill_spend_workspace"
            )
        ]


class HubSkillSpendPeriod(ContextRecord):
    policy = models.ForeignKey(
        HubSkillSpendPolicy, on_delete=models.PROTECT, related_name="hub_skill_periods"
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "starts_at"], name="hub_skill_spend_period"
            )
        ]


class HubSkillSpendReservation(ContextRecord):
    period = models.ForeignKey(
        HubSkillSpendPeriod,
        on_delete=models.PROTECT,
        related_name="hub_skill_reservations",
    )
    operation_id = models.UUIDField()
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, related_name="hub_skill_calls"
    )
    source = models.CharField(max_length=24)
    source_id = models.UUIDField()
    kind = models.CharField(max_length=16)
    state = models.CharField(max_length=16, default="prepared")
    claim_generation = models.PositiveBigIntegerField(default=0)
    reserved_usd = models.DecimalField(max_digits=18, decimal_places=8)
    settled_usd = models.DecimalField(max_digits=18, decimal_places=8, null=True)
    sender_active = models.BooleanField(default=False)
    settled_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "operation_id"], name="hub_skill_spend_operation"
            )
        ]

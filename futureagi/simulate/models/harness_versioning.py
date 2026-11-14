"""Versioned pieces of a hosted harness environment: environment, suite, agent runtime.

None of these tables is CDC-mirrored. A version's content lives in an immutable,
content-addressed archive in object storage; these rows index it.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class VersionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    DISCARDED = "discarded", "Discarded"


class VersionOrigin(models.TextChoices):
    AUTHORING = "authoring", "Authoring"
    NUDGE = "nudge", "Nudge"
    MANUAL = "manual", "Manual"
    CHAT = "chat", "Chat"
    BACKFILL = "backfill", "Backfill"
    EXTEND = "extend", "Extend"


class HashKind(models.TextChoices):
    TREE_V1 = "tree_v1", "Tree v1"
    LEGACY_COMBINED = "legacy_combined", "Legacy combined"
    LEGACY_KEY = "legacy_key", "Legacy key"


class ArchiveLayout(models.TextChoices):
    SPLIT = "split", "Split"
    COMBINED = "combined", "Combined"


class DiscardReason(models.TextChoices):
    USER = "user", "User"
    SET_ASIDE_BY_REBUILD = "set_aside_by_rebuild", "Set aside by rebuild"
    REBUILD_RESTORED = "rebuild_restored", "Rebuild restored"


class _HarnessPieceVersion(BaseModel):
    """Fields shared by environment and suite versions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Assigned when the version becomes active, so drafts never use a number.
    version_number = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=VersionStatus.choices, db_index=True
    )
    origin = models.CharField(max_length=24, choices=VersionOrigin.choices)
    hash = models.CharField(max_length=71, db_index=True)
    hash_kind = models.CharField(
        max_length=24, choices=HashKind.choices, default=HashKind.TREE_V1
    )
    archive_object_key = models.CharField(max_length=1024)
    archive_layout = models.CharField(
        max_length=16, choices=ArchiveLayout.choices, default=ArchiveLayout.SPLIT
    )
    archive_sha256 = models.CharField(max_length=64)
    archive_size = models.BigIntegerField()
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    summary = models.CharField(max_length=1024, blank=True, default="")
    diff = models.JSONField(null=True, blank=True)
    # Secret-safe snapshots of this piece's stage outputs, served without the object store.
    documents = models.JSONField(default=dict, blank=True)
    nudge = models.ForeignKey(
        "simulate.HarnessNudge",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    discard_reason = models.CharField(
        max_length=24, choices=DiscardReason.choices, null=True, blank=True
    )
    # A draft's edit counter, bumped on every draft write.
    revision = models.PositiveIntegerField(default=0)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    includes_draft_edits = models.BooleanField(default=False)
    snapshot_diff = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True


class HarnessEnvironmentVersion(_HarnessPieceVersion):
    """Contract, world and sub-goal catalogue of one environment, as one version."""

    contract_hash_raw = models.CharField(max_length=71, blank=True, default="")
    world_ir_hash = models.CharField(max_length=71, blank=True, default="")
    agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_harness_environment_version"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "version_number"],
                condition=Q(version_number__isnull=False),
                name="uniq_harness_env_version_number",
            ),
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(status="draft", deleted=False),
                name="uniq_harness_env_version_draft",
            ),
            models.UniqueConstraint(
                fields=["job", "hash", "hash_kind"],
                condition=Q(status="active", deleted=False),
                name="uniq_harness_env_version_hash",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "status"], name="idx_henvver_job_status"),
            models.Index(fields=["job", "-created_at"], name="idx_henvver_job_created"),
        ]


class HarnessSuiteVersion(_HarnessPieceVersion):
    """The scenario suite of one environment, as one version."""

    certify_hash = models.CharField(
        max_length=71, db_index=True, blank=True, default=""
    )
    scenario_count = models.PositiveSmallIntegerField(default=0)
    authored_against = models.ForeignKey(
        HarnessEnvironmentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_harness_suite_version"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "version_number"],
                condition=Q(version_number__isnull=False),
                name="uniq_harness_suite_version_number",
            ),
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(status="draft", deleted=False),
                name="uniq_harness_suite_version_draft",
            ),
            models.UniqueConstraint(
                fields=["job", "hash", "hash_kind"],
                condition=Q(status="active", deleted=False),
                name="uniq_harness_suite_version_hash",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "status"], name="idx_hsuitever_job_status"),
            models.Index(
                fields=["job", "-created_at"], name="idx_hsuitever_job_created"
            ),
        ]


class HarnessSuiteMember(BaseModel):
    """One scenario's contents inside one suite version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    suite_version = models.ForeignKey(
        HarnessSuiteVersion, on_delete=models.CASCADE, related_name="members"
    )
    # RESTRICT, not PROTECT, so a cascade from the job removes both together.
    scenario = models.ForeignKey(
        "simulate.HostedHarnessScenario",
        on_delete=models.RESTRICT,
        related_name="+",
    )
    scenario_key = models.CharField(max_length=255)
    number = models.PositiveIntegerField()
    content = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "simulate_harness_suite_member"
        constraints = [
            models.UniqueConstraint(
                fields=["suite_version", "scenario_key"],
                name="uniq_harness_suite_member_key",
            ),
        ]
        indexes = [
            models.Index(fields=["scenario_key"], name="idx_hsuitemember_key"),
        ]


class HarnessSuiteValidation(BaseModel):
    """Proof of a suite version against an environment version."""

    class Status(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    suite_version = models.ForeignKey(
        HarnessSuiteVersion, on_delete=models.CASCADE, related_name="validations"
    )
    environment_version = models.ForeignKey(
        HarnessEnvironmentVersion,
        on_delete=models.CASCADE,
        related_name="suite_validations",
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    proofs = models.JSONField(default=dict, blank=True)
    job = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    proved_at = models.DateTimeField()

    class Meta:
        db_table = "simulate_harness_suite_validation"
        constraints = [
            models.UniqueConstraint(
                fields=["suite_version", "environment_version"],
                name="uniq_harness_suite_validation_pair",
            ),
        ]


class HarnessAgentVersionRuntime(BaseModel):
    """Harness-side facts of one agent version: source, payload and credential set."""

    class SourceKind(models.TextChoices):
        GITHUB = "github", "GitHub"
        ARCHIVE = "archive", "Archive"
        PROVIDER = "provider", "Provider"
        PHONE = "phone", "Phone"
        LIVEKIT = "livekit", "LiveKit"

    class PreflightStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        LEGACY = "legacy", "Legacy"
        DISCARDED = "discarded", "Discarded"

    class Trigger(models.TextChoices):
        CREATION = "creation", "Creation"
        PUSH = "push", "Push"
        UPLOAD = "upload", "Upload"
        PROMPT = "prompt", "Prompt"
        AGENT_PAYLOAD = "agent_payload", "Agent payload"
        CREDENTIALS = "credentials", "Credentials"
        PROVIDER_SYNC = "provider_sync", "Provider sync"
        BACKFILL = "backfill", "Backfill"
        FIX = "fix", "Fix my agent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_version = models.OneToOneField(
        "simulate.AgentVersion",
        on_delete=models.CASCADE,
        related_name="harness_runtime",
    )
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    hash = models.CharField(max_length=71, db_index=True)
    prompt_hash = models.CharField(max_length=71, null=True, blank=True)
    source_kind = models.CharField(max_length=16, choices=SourceKind.choices)
    source = models.JSONField(default=dict, blank=True)
    source_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    agent_payload = models.JSONField(default=dict, blank=True)
    credential_blob = models.TextField(null=True, blank=True)
    credential_set_hash = models.CharField(max_length=71, null=True, blank=True)
    credential_fingerprints = models.JSONField(default=dict, blank=True)
    credential_names_hash = models.CharField(max_length=71, null=True, blank=True)
    credential_names = models.JSONField(default=list, blank=True)
    # NULL means unknown (pre-versioning agents); checks skip it.
    required_variable_names = models.JSONField(null=True, blank=True)
    preflight_status = models.CharField(
        max_length=16,
        choices=PreflightStatus.choices,
        default=PreflightStatus.PENDING,
    )
    preflight_report = models.JSONField(null=True, blank=True)
    preflighted_at = models.DateTimeField(null=True, blank=True)
    # When the latest check was asked for: on creation, a retry or a restart.
    check_started_at = models.DateTimeField(null=True, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    summary = models.CharField(max_length=1024, blank=True, default="")
    diff = models.JSONField(null=True, blank=True)
    trigger = models.CharField(max_length=24, choices=Trigger.choices)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_harness_agent_version_runtime"
        constraints = [
            # Legacy rows are backfilled per version and soft-deleted rows are history;
            # neither may block a live version with the same identity.
            models.UniqueConstraint(
                fields=["agent_definition", "hash"],
                condition=Q(deleted=False)
                & ~Q(preflight_status__in=["failed", "discarded", "legacy"]),
                name="uniq_harness_agent_runtime_hash",
            ),
        ]

    def save(self, *args, **kwargs):
        from agentcc.services.credential_manager import encrypt_token

        if self.credential_blob and not self.credential_blob.startswith("enc::"):
            self.credential_blob = encrypt_token(self.credential_blob)
        super().save(*args, **kwargs)

    def get_credentials(self) -> str | None:
        from agentcc.services.credential_manager import decrypt_token

        if not self.credential_blob:
            return None
        return decrypt_token(self.credential_blob)


class HarnessNudge(BaseModel):
    """A dependency changed: offer to rebuild the environment or its suite."""

    class Target(models.TextChoices):
        ENVIRONMENT = "environment", "Environment"
        SUITE = "suite", "Suite"

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        ANSWERED_NO = "answered_no", "Answered no"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        APPLIED = "applied", "Applied"
        NO_CHANGE = "no_change", "No change"
        REPLACED = "replaced", "Replaced"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    target = models.CharField(max_length=16, choices=Target.choices)
    state = models.CharField(
        max_length=24, choices=State.choices, default=State.PENDING
    )
    # Changed files and variable names; never values.
    facts = models.JSONField(default=dict, blank=True)
    trigger_agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    base_agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    base_environment_version = models.ForeignKey(
        HarnessEnvironmentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    base_suite_version = models.ForeignKey(
        HarnessSuiteVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    result_environment_version = models.ForeignKey(
        HarnessEnvironmentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    result_suite_version = models.ForeignKey(
        HarnessSuiteVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    conversation_message = models.ForeignKey(
        "simulate.HostedHarnessConversationMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # The builder message a rebuild dispatched; ``conversation_message`` is the card.
    command_message = models.ForeignKey(
        "simulate.HostedHarnessConversationMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    summary = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    usage = models.JSONField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    # The piece's draft before the rebuild; restored on any outcome that does not land.
    draft_snapshot = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "simulate_harness_nudge"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "target"],
                condition=Q(state="pending"),
                name="uniq_harness_nudge_pending",
            ),
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(state__in=["queued", "running"]),
                name="uniq_harness_nudge_running",
            ),
        ]
        indexes = [
            models.Index(
                fields=["job", "target", "state"], name="idx_hnudge_job_target"
            ),
        ]


class HarnessAssembledBundle(BaseModel):
    """Cache of a sealed bundle for one agent × environment × suite combination."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    agent_version = models.ForeignKey(
        "simulate.AgentVersion", on_delete=models.CASCADE, related_name="+"
    )
    environment_version = models.ForeignKey(
        HarnessEnvironmentVersion, on_delete=models.CASCADE, related_name="+"
    )
    suite_version = models.ForeignKey(
        HarnessSuiteVersion, on_delete=models.CASCADE, related_name="+"
    )
    assembly_key = models.CharField(max_length=71)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING
    )
    bundle_digest = models.CharField(
        max_length=71, null=True, blank=True, db_index=True
    )
    object_key = models.CharField(max_length=1024, null=True, blank=True)
    sha256 = models.CharField(max_length=64, null=True, blank=True)
    size = models.BigIntegerField(null=True, blank=True)
    certificate = models.JSONField(null=True, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    built_by_attempt = models.ForeignKey(
        "simulate.HostedHarnessAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    error = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "simulate_harness_assembled_bundle"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "assembly_key"],
                name="uniq_harness_bundle_assembly_key",
            ),
        ]

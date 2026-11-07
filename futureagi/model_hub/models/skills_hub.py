"""Workspace-owned skills, immutable versions and mutable authoring state."""

import uuid

from django.conf import settings
from django.db import models

from tfc.utils.base_model import BaseModel


class HubSkill(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="hub_skills"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace", on_delete=models.CASCADE, related_name="hub_skills"
    )
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=1024)
    tags = models.JSONField(default=list)
    latest_version_number = models.PositiveIntegerField(default=0)
    source = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skills_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skills_updated",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=models.Q(deleted=False),
                name="uniq_hub_skill_ws_name_live",
            )
        ]
        indexes = [
            models.Index(
                fields=["workspace", "updated_at"], name="hub_skill_ws_updated_idx"
            ),
            models.Index(
                fields=["organization", "workspace", "deleted"],
                name="hub_skill_org_ws_deleted_idx",
            ),
        ]


class HubSkillVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        HubSkill, on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField()
    digest = models.CharField(max_length=71)
    note = models.TextField(max_length=2000, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_versions",
    )
    created_by_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_versions",
    )
    file_count = models.PositiveIntegerField()
    total_bytes = models.PositiveIntegerField()
    frontmatter = models.JSONField()
    checks = models.JSONField()
    parent_number = models.PositiveIntegerField(null=True)
    source = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "number"], name="uniq_hub_skill_version_number"
            )
        ]
        indexes = [
            models.Index(
                fields=["skill", "-number"], name="hub_skill_version_number_idx"
            )
        ]


class HubSkillBlob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="hub_skill_blobs",
    )
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sha256"], name="uniq_hub_skill_blob_org_sha"
            )
        ]


class HubSkillFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        HubSkillVersion, on_delete=models.CASCADE, related_name="files"
    )
    path = models.CharField(max_length=255)
    blob = models.ForeignKey(
        HubSkillBlob, on_delete=models.PROTECT, related_name="files"
    )
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    token_estimate = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "path"], name="uniq_hub_skill_file_path"
            )
        ]


class HubSkillLabel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(HubSkill, on_delete=models.CASCADE, related_name="labels")
    name = models.CharField(max_length=40)
    version = models.ForeignKey(
        HubSkillVersion, on_delete=models.PROTECT, related_name="labels"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_labels",
    )
    updated_by_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_labels",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "name"], name="uniq_hub_skill_label"
            )
        ]


class HubSkillLabelEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        HubSkill, on_delete=models.CASCADE, related_name="label_events"
    )
    label = models.CharField(max_length=40)
    from_version = models.PositiveIntegerField(null=True)
    to_version = models.PositiveIntegerField(null=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_label_events",
    )
    actor_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_label_events",
    )
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["skill", "-created_at"], name="hub_skill_label_event_time_idx"
            )
        ]


class HubSkillDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(HubSkill, on_delete=models.CASCADE, related_name="drafts")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_drafts",
    )
    base_version = models.PositiveIntegerField(null=True)
    files = models.JSONField(default=list)
    note = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "user"], name="uniq_hub_skill_draft_user"
            )
        ]


class HubSkillUsageDaily(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        HubSkill, on_delete=models.CASCADE, related_name="usage_days"
    )
    version = models.ForeignKey(
        HubSkillVersion, on_delete=models.CASCADE, related_name="usage_days"
    )
    day = models.DateField()
    channel = models.CharField(
        max_length=16,
        choices=[("api", "api"), ("download", "download"), ("mcp", "mcp")],
    )
    consumer_key = models.CharField(max_length=80)
    consumer_label = models.CharField(max_length=200)
    count = models.PositiveIntegerField()
    last_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "day", "channel", "consumer_key"],
                name="uniq_hub_skill_usage_day",
            )
        ]
        indexes = [
            models.Index(fields=["skill", "day"], name="hub_skill_usage_day_idx")
        ]

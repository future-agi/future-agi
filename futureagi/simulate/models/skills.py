import uuid

from django.db import models
from django.db.models import Q

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class SkillKind(models.TextChoices):
    SKILL = "skill", "Skill"
    CONTEXT = "context", "Agent context"


class AgentSkillVersion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition", on_delete=models.CASCADE, related_name="+"
    )
    kind = models.CharField(
        max_length=16, choices=SkillKind.choices, default=SkillKind.SKILL
    )
    path = models.CharField(max_length=512)
    version_number = models.PositiveIntegerField()
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    readable = models.BooleanField(default=True)
    problem = models.CharField(max_length=64, blank=True, default="")
    files_hash = models.CharField(max_length=64)
    files_manifest = models.JSONField(default=list)
    size = models.PositiveIntegerField()

    class Meta:
        db_table = "simulate_agent_skill_version"
        ordering = ("path", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=["agent_definition", "path", "version_number"],
                condition=Q(deleted=False),
                name="uniq_agent_skill_version_number",
            ),
        ]


class AgentVersionSkillScanStatus(models.TextChoices):
    SCANNED = "scanned", "Scanned"
    SOURCE_MISSING = "source_missing", "Source missing"
    TOO_LARGE = "too_large", "Too large to read"


class ContextRuntime(models.TextChoices):
    CLAUDE = "claude", "Claude Agent SDK"
    CODEX = "codex", "Codex"
    GENERIC = "generic", "Other"


class AgentVersionSkillScan(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_version = models.OneToOneField(
        "simulate.AgentVersion", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    source_key = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=AgentVersionSkillScanStatus.choices
    )
    skill_versions = models.ManyToManyField(
        AgentSkillVersion, related_name="+", blank=True
    )
    runtime = models.CharField(
        max_length=16, choices=ContextRuntime.choices, default=ContextRuntime.GENERIC
    )
    context_capped = models.BooleanField(default=False)

    class Meta:
        db_table = "simulate_agent_version_skill_scan"


class AgentVersionContextFile(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scan = models.ForeignKey(
        AgentVersionSkillScan, on_delete=models.CASCADE, related_name="context_files"
    )
    skill_version = models.ForeignKey(
        AgentSkillVersion, on_delete=models.CASCADE, related_name="+"
    )
    position = models.PositiveIntegerField()
    rule = models.CharField(max_length=32)
    on_demand = models.BooleanField(default=False)
    folder = models.CharField(max_length=512, blank=True, default="")
    imported_from = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        db_table = "simulate_agent_version_context_file"
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["scan", "position"], name="uniq_agent_version_context_position"
            ),
        ]

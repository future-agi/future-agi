import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class SkillKind(models.TextChoices):
    SKILL = "skill", "Skill"
    CONTEXT = "context", "Agent context"


class SkillTagName(models.TextChoices):
    STAGING = "staging", "Staging"
    PRODUCTION = "production", "Production"


class Skill(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    kind = models.CharField(
        max_length=16, choices=SkillKind.choices, default=SkillKind.SKILL
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_skill"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "name"],
                condition=Q(deleted=False, workspace__isnull=False),
                name="uniq_skill_name_workspace",
            ),
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=Q(deleted=False, workspace__isnull=True),
                name="uniq_skill_name_no_workspace",
            ),
        ]


class SkillOrigin(models.TextChoices):
    LIBRARY = "library", "Library"
    REPO = "repo", "Agent's code"


class SkillVersion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, null=True, blank=True, related_name="versions"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    origin = models.CharField(
        max_length=16, choices=SkillOrigin.choices, default=SkillOrigin.LIBRARY
    )
    kind = models.CharField(
        max_length=16, choices=SkillKind.choices, default=SkillKind.SKILL
    )
    path = models.CharField(max_length=512, blank=True, default="")
    readable = models.BooleanField(default=True)
    problem = models.CharField(max_length=64, blank=True, default="")
    version_number = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    files_hash = models.CharField(max_length=64)
    files_manifest = models.JSONField(default=list)
    object_key = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveIntegerField()
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_skill_version"
        ordering = ("-version_number",)
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "version_number"], name="uniq_skill_version_number"
            ),
            models.UniqueConstraint(
                fields=["organization", "files_hash", "path"],
                condition=Q(origin="repo", deleted=False),
                name="uniq_repo_skill_version",
            ),
        ]


class AgentVersionSkillScanStatus(models.TextChoices):
    SCANNED = "scanned", "Scanned"
    SOURCE_MISSING = "source_missing", "Source missing"
    TOO_LARGE = "too_large", "Too large to read"


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
    skill_versions = models.ManyToManyField(SkillVersion, related_name="+", blank=True)

    class Meta:
        db_table = "simulate_agent_version_skill_scan"


class SkillTag(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=16, choices=SkillTagName.choices)
    skill_version = models.ForeignKey(
        SkillVersion, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "simulate_skill_tag"
        constraints = [
            models.UniqueConstraint(fields=["skill", "name"], name="uniq_skill_tag"),
        ]


class SkillPromotion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill_version = models.ForeignKey(
        SkillVersion, on_delete=models.CASCADE, related_name="promotions"
    )
    tag = models.CharField(max_length=16, choices=SkillTagName.choices)
    tested = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_skill_promotion"
        ordering = ("-created_at",)


class SkillAttachment(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="attachments"
    )
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition", on_delete=models.CASCADE, related_name="+"
    )
    tag = models.CharField(
        max_length=16, choices=SkillTagName.choices, blank=True, default=""
    )
    skill_version = models.ForeignKey(
        SkillVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_skill_attachment"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "agent_definition"],
                condition=Q(deleted=False),
                name="uniq_skill_attachment",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(tag="") & Q(skill_version__isnull=False))
                    | (~Q(tag="") & Q(skill_version__isnull=True))
                ),
                name="skill_attachment_tag_xor_version",
            ),
        ]


class SkillTestState(models.TextChoices):
    RUNNING = "running", "Running"
    FINISHED = "finished", "Finished"
    FAILED = "failed", "Failed"


class SkillTestVerdict(models.TextChoices):
    HELPS = "helps", "Helps"
    HELPS_WITH_REGRESSION = "helps_with_regression", "Helps, with a regression"
    NO_IMPROVEMENT = "no_improvement", "No improvement"
    NOT_USED = "not_used", "Not used"
    INCONCLUSIVE = "inconclusive", "Inconclusive"


class SkillTest(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill_version = models.ForeignKey(
        SkillVersion, on_delete=models.CASCADE, related_name="tests"
    )
    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition", on_delete=models.CASCADE, related_name="+"
    )
    environment = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    run_a = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    run_b = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    baseline_version = models.ForeignKey(
        SkillVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    scenario_keys = models.JSONField(default=list)
    trials = models.PositiveSmallIntegerField(default=1)
    state = models.CharField(
        max_length=16, choices=SkillTestState.choices, default=SkillTestState.RUNNING
    )
    verdict = models.CharField(
        max_length=32, choices=SkillTestVerdict.choices, blank=True, default=""
    )
    summary = models.JSONField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_skill_test"
        ordering = ("-created_at",)

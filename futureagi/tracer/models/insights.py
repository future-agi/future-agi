import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class InsightUnitType(models.TextChoices):
    TRACES = "traces", "Traces"
    SESSIONS = "sessions", "Sessions"
    USERS = "users", "Users"


class InsightLens(models.TextChoices):
    USAGE = "usage", "What users are trying to do"
    FAILURES = "failures", "Where the agent falls short"
    EXPERIENCE = "experience", "How the experience feels"
    PERSONAS = "personas", "Who the users are"
    CUSTOM = "custom", "Custom question"


class InsightConfig(BaseModel):
    """A saved Insights report: what to read, through which lens, and the
    taxonomy its runs share.

    The first completed run freezes its categories into ``taxonomy`` so later
    runs classify into the same categories and their shares stay comparable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="insight_configs"
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="insight_configs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="insight_configs",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="insight_configs",
    )
    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=16, choices=InsightUnitType.choices)
    lens = models.CharField(max_length=16, choices=InsightLens.choices)
    question = models.TextField()
    agent_description = models.TextField(blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    attributes = models.JSONField(default=list, blank=True)
    taxonomy = models.JSONField(default=dict, blank=True)
    scope = models.JSONField(default=dict, blank=True)
    sample_size = models.PositiveIntegerField(default=300)

    class Meta:
        db_table = "tracer_insight_config"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "deleted"])]

    def __str__(self):
        return f"Insights config {self.name}"


class InsightRun(BaseModel):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(
        InsightConfig, on_delete=models.CASCADE, related_name="runs"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="insight_runs"
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="insight_runs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="insight_runs",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="insight_runs",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    stage = models.CharField(max_length=32, blank=True, default="queued")
    progress = models.JSONField(default=dict, blank=True)
    unit = models.CharField(max_length=16, choices=InsightUnitType.choices)
    scope = models.JSONField(default=dict, blank=True)
    # Snapshot of the config used, so editing the config never rewrites history.
    settings = models.JSONField(default=dict, blank=True)
    population_size = models.PositiveIntegerField(default=0)
    sample_size = models.PositiveIntegerField(default=0)
    analysed_count = models.PositiveIntegerField(default=0)
    taxonomy_mode = models.CharField(max_length=16, default="discover")
    result = models.JSONField(default=dict, blank=True)
    models_used = models.JSONField(default=dict, blank=True)
    cost_usd = models.FloatField(default=0.0)
    error = models.TextField(blank=True, default="")
    workflow_id = models.CharField(max_length=255, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_insight_run"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["config", "created_at"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self):
        return f"Insights run {self.id} ({self.status})"


class InsightUnit(models.Model):
    """One analysed trace, session or user within a run: what was read, the
    facets extracted from it and the category it landed in."""

    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey(InsightRun, on_delete=models.CASCADE, related_name="units")
    unit_id = models.CharField(max_length=255)
    label = models.CharField(max_length=255, blank=True, default="")
    user_id = models.CharField(max_length=255, blank=True, default="")
    session_id = models.CharField(max_length=255, blank=True, default="")
    time = models.DateTimeField(null=True, blank=True)
    excerpt = models.TextField(blank=True, default="")
    content = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")
    lens_answer = models.TextField(blank=True, default="")
    topic = models.CharField(max_length=255, blank=True, default="")
    facets = models.JSONField(default=dict, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    included = models.BooleanField(default=True)
    category_id = models.CharField(max_length=64, blank=True, default="")
    sub_category_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, default="pending")
    error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "tracer_insight_unit"
        indexes = [
            models.Index(fields=["run", "status"]),
            models.Index(fields=["run", "category_id"]),
            models.Index(fields=["run", "sub_category_id"]),
        ]

    def __str__(self):
        return f"Insights unit {self.unit_id}"

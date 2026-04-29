import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evaluation import Evaluation
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class EvaluationRun(BaseModel):
    """
    Represents a single CI/CD evaluation run for a specific version of a project.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="evaluation_runs"
    )
    version = models.CharField(
        max_length=255,
        help_text="The version identifier, e.g., a git commit hash or version tag.",
    )
    eval_data = models.JSONField(default=dict, blank=True, null=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="evaluation_runs",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "tracer_evaluation_run"
        unique_together = ("project", "version")
        indexes = [
            models.Index(fields=["project", "version"]),
        ]

    def __str__(self):
        return f"Run for {self.project.name} - {self.version}"


class EvaluationResult(BaseModel):
    """
    Links an individual Evaluation to a specific EvaluationRun.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation_run = models.ForeignKey(
        EvaluationRun, on_delete=models.CASCADE, related_name="results"
    )
    evaluation = models.OneToOneField(
        Evaluation, on_delete=models.CASCADE, related_name="ci_cd_result"
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="evaluation_results",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "tracer_evaluation_result"
        indexes = [
            models.Index(fields=["evaluation_run"]),
        ]

    def __str__(self):
        return f"Result for Run {self.evaluation_run.id}"

import uuid

from django.db import models

from model_hub.models.develop_annotations import Annotations
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class ProjectVersion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="versions",
        blank=False,
        null=False,
    )
    name = models.CharField(max_length=255, blank=False, null=False)
    version = models.CharField(
        max_length=10, blank=False, null=False
    )  # Stores v1, v2, v3 etc
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    eval_tags = models.JSONField(null=True, blank=True, default=list)
    avg_eval_score = models.FloatField(null=True, blank=True)
    config = models.JSONField(null=True, blank=True, default=list)
    annotations = models.ForeignKey(
        Annotations,
        on_delete=models.CASCADE,
        related_name="annotations",
        blank=True,
        null=True,
    )

    def __str__(self):
        return f"{self.project.name} - {self.version}"

    class Meta:
        db_table = "tracer_project_version"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version"],
                condition=models.Q(deleted=False),
                name="unique_version_per_project",
            )
        ]
        indexes = [
            models.Index(fields=["project", "version"]),
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["end_time"]),
        ]


class ProjectVersionWinner(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="version_winner",
        blank=False,
        null=False,
    )
    eval_config = models.JSONField(null=True, blank=True)
    winner_version = models.ForeignKey(
        ProjectVersion,
        on_delete=models.CASCADE,
        related_name="winner_version",
        blank=False,
        null=False,
    )
    version_mapper = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "tracer_project_version_winner"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["winner_version"]),
        ]

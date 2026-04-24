import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccProject(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_projects",
        blank=False,
        null=False,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_projects",
        null=True,
        blank=True,
    )
    tracer_project = models.OneToOneField(
        "tracer.Project",
        on_delete=models.SET_NULL,
        related_name="agentcc_project",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "agentcc_project"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_project_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
        ]

    def __str__(self):
        return self.name

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization, User, Workspace
from tfc.utils.base_model import BaseModel


class Graph(BaseModel):
    """
    Identity container for agent graphs.

    Graphs can be referenced by other graphs via subgraph nodes.
    Template graphs (is_template=True) are system-defined reusable blueprints
    with no organization, workspace, or created_by.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agent_playground_graphs",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agent_playground_graphs",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255, help_text="Display name")
    description = models.TextField(null=True, blank=True)
    is_template = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="created_agent_playground_graphs",
        null=True,
        blank=True,
    )
    collaborators = models.ManyToManyField(
        User,
        related_name="collaborated_agent_playground_graphs",
        blank=True,
    )

    class Meta:
        db_table = "agent_playground_graph"
        indexes = [
            models.Index(fields=["workspace"]),
            models.Index(fields=["organization"]),
            models.Index(fields=["is_template"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.is_template:
            if self.organization_id or self.workspace_id or self.created_by_id:
                raise ValidationError(
                    "Template graphs must not have organization, workspace, or created_by."
                )
        else:
            if not self.organization_id:
                raise ValidationError("Non-template graphs must have an organization.")
            if not self.created_by_id:
                raise ValidationError(
                    "Non-template graphs must have a created_by user."
                )
        if self.created_by_id and self.organization_id:
            if not self.created_by.can_access_organization(self.organization):
                raise ValidationError(
                    "created_by user must belong to the same organization as the graph."
                )

    def save(self, *args, **kwargs):
        self.clean()
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and self.created_by_id:
            self.collaborators.add(self.created_by)

    def add_collaborator(self, user):
        """Add a collaborator to this graph."""
        self.collaborators.add(user)

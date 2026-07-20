import uuid

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization, User, Workspace
from tfc.utils.base_model import BaseModel


class Graph(BaseModel):
    """
    Identity container for agent graphs.

    Graphs can be referenced by other graphs via subgraph nodes.

    Template graphs (is_template=True) come in two scopes:
    - System templates: no organization, workspace, or created_by. Visible to all orgs.
    - Org-scoped templates: has organization + created_by. Visible within that org only.
      Created by users via "Publish as template" — enables sharing reusable sub-graphs.
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
    tags = ArrayField(
        models.CharField(max_length=50),
        default=list,
        blank=True,
        help_text="Searchable labels for template discovery (e.g. 'rag', 'safety', 'classification')",
    )

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
            models.Index(fields=["is_template", "organization"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.is_template:
            if self.workspace_id:
                raise ValidationError("Template graphs must not have a workspace.")
            # System templates: no org/created_by (visible to all orgs).
            # Org-scoped templates: must have both org and created_by.
            has_org = bool(self.organization_id)
            has_creator = bool(self.created_by_id)
            if has_org != has_creator:
                raise ValidationError(
                    "Org-scoped templates must have both organization and created_by, "
                    "or neither (system template)."
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

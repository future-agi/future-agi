import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class SavedView(BaseModel):
    TAB_TYPE_CHOICES = (
        ("traces", "Traces"),
        ("spans", "Spans"),
        ("voice", "Voice"),
        ("imagine", "Imagine"),
        ("users", "Users"),
        ("user_detail", "User Detail"),
        ("sessions", "Sessions"),
    )

    VISIBILITY_CHOICES = (
        ("personal", "Personal"),
        ("project", "Project"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="saved_views",
        blank=True,
        null=True,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="saved_views",
        blank=False,
        null=False,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_saved_views",
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_saved_views",
    )
    name = models.CharField(max_length=255)
    tab_type = models.CharField(max_length=20, choices=TAB_TYPE_CHOICES)
    visibility = models.CharField(
        max_length=20, choices=VISIBILITY_CHOICES, default="personal"
    )
    revision = models.PositiveIntegerField(default=1)
    position = models.IntegerField(default=0)
    icon = models.CharField(max_length=50, blank=True, null=True)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "tracer_saved_view"
        ordering = ["position", "created_at"]
        indexes = [
            models.Index(fields=["project", "created_by", "visibility"]),
            models.Index(fields=["project", "visibility"]),
            models.Index(fields=["workspace", "created_by", "tab_type"]),
        ]
        constraints = [
            # Uniqueness for project-scoped views
            models.UniqueConstraint(
                fields=["project", "created_by", "name"],
                condition=models.Q(deleted=False, project__isnull=False),
                name="unique_saved_view_name_per_user_project",
            ),
            # Uniqueness for workspace-scoped (project-null) views, per tab_type
            models.UniqueConstraint(
                fields=["workspace", "created_by", "tab_type", "name"],
                condition=models.Q(deleted=False, project__isnull=True),
                name="unique_saved_view_name_per_user_workspace",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.tab_type})"

    @classmethod
    def scoped(cls, created_by, *, project, workspace, tab_type):
        """Non-deleted views in one uniqueness/position bucket (mirrors Meta.constraints)."""
        qs = cls.objects.filter(created_by=created_by, deleted=False)
        if project is not None:
            return qs.filter(project=project)
        return qs.filter(
            project__isnull=True, workspace=workspace, tab_type=tab_type
        )


class SavedViewTabOrder(BaseModel):
    """A caller's order within a project or a workspace-only tab bucket."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_view_tab_orders")
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="saved_view_tab_orders")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name="saved_view_tab_orders")
    tab_type = models.CharField(max_length=20, choices=SavedView.TAB_TYPE_CHOICES, null=True, blank=True)
    order = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "tracer_saved_view_tab_order"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "workspace", "project"],
                condition=models.Q(deleted=False, project__isnull=False),
                name="unique_saved_view_tab_order_per_user_project",
            ),
            models.UniqueConstraint(
                fields=["user", "workspace", "tab_type"],
                condition=models.Q(deleted=False, project__isnull=True),
                name="unique_saved_view_tab_order_per_user_workspace_tab",
            ),
        ]
        indexes = [models.Index(fields=["user", "workspace", "project"])]

    def clean(self):
        super().clean()
        if self.project_id is not None and self.tab_type is not None:
            raise ValidationError({"tab_type": "Project orders cannot specify tab_type."})
        if self.project_id is None and not self.tab_type:
            raise ValidationError({"tab_type": "Workspace orders require tab_type."})

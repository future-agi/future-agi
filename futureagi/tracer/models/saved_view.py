import uuid

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

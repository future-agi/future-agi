import uuid

from django.db import models

from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class Dashboard(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="dashboards",
        blank=False,
        null=False,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_dashboards",
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="updated_dashboards",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "-created_at"]),
        ]

    def __str__(self):
        return self.name


class DashboardWidget(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dashboard = models.ForeignKey(
        Dashboard,
        on_delete=models.CASCADE,
        related_name="widgets",
    )
    name = models.CharField(max_length=255, default="Untitled")
    description = models.TextField(blank=True, default="")
    position = models.IntegerField(default=0)
    width = models.IntegerField(default=12)
    height = models.IntegerField(default=4)
    query_config = models.JSONField(default=dict)
    chart_config = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_dashboard_widgets",
    )

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self):
        return f"{self.dashboard.name} - {self.name}"

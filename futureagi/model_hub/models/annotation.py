import uuid

from django.db import models

from accounts.models import User
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models import AIModel


class AnnotationTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_name = models.CharField(max_length=255)
    assigned_users = models.ManyToManyField(User, related_name="annotation_tasks")
    ai_model = models.ForeignKey(
        AIModel, on_delete=models.CASCADE, related_name="annotation_tasks"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="organization"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotation_tasks",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"Annotation Task {self.id}"


class ClickHouseAnnotation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uuid: models.CharField = models.CharField(
        max_length=36
    )  # UUIDs are 36 characters long
    annotation_task = models.ForeignKey(
        AnnotationTask,
        on_delete=models.CASCADE,
        related_name="clickhouse_annotations",
    )
    is_annotated = models.BooleanField(default=False)

    def __str__(self):
        return self.uuid

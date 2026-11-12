"""Persisted personal or workspace-shared grid configurations."""

import uuid

from django.conf import settings
from django.db import models


class DatasetSavedView(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        "model_hub.Dataset", on_delete=models.CASCADE, related_name="saved_grid_views"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    state = models.JSONField(default=dict)
    shared = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "model_hub_dataset_saved_view"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "created_by", "name"],
                condition=models.Q(deleted=False),
                name="unique_dataset_view_name_per_user",
            )
        ]

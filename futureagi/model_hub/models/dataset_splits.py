"""Named sets of current rows, independent of dataset versions."""

import uuid

from django.db import models


class DatasetSplit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        "model_hub.Dataset", on_delete=models.CASCADE, related_name="splits"
    )
    name = models.CharField(max_length=30)
    color = models.CharField(max_length=20, default="info")
    description = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "name"], name="dataset_split_name_unique"
            )
        ]


class DatasetSplitMembership(models.Model):
    split = models.ForeignKey(
        DatasetSplit, on_delete=models.CASCADE, related_name="memberships"
    )
    row = models.ForeignKey(
        "model_hub.Row", on_delete=models.CASCADE, related_name="split_memberships"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["split", "row"], name="dataset_split_row_unique"
            )
        ]

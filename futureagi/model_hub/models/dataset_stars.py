"""Personal bookmarks; dataset visibility remains authoritative."""

from django.conf import settings
from django.db import models


class DatasetStar(models.Model):
    dataset = models.ForeignKey(
        "model_hub.Dataset", on_delete=models.CASCADE, related_name="stars"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dataset_stars"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dataset"], name="unique_personal_dataset_star"
            ),
        ]

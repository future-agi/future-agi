"""Inputs consumed by the last completed generation of an output cell."""

from django.db import models


class GeneratedInputSnapshot(models.Model):
    column = models.ForeignKey("model_hub.Column", on_delete=models.CASCADE)
    row = models.ForeignKey("model_hub.Row", on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=71)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["column", "row"], name="unique_generated_input_snapshot"
            )
        ]

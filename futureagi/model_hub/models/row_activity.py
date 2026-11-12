"""Append-only row history, populated atomically by PostgreSQL write triggers."""

from django.db import models
from django.utils import timezone


class RowActivity(models.Model):
    id = models.BigAutoField(primary_key=True)
    row = models.ForeignKey(
        "model_hub.Row", on_delete=models.CASCADE, related_name="activity_events"
    )
    at = models.DateTimeField(default=timezone.now, editable=False)
    subject = models.CharField(max_length=4, choices=[("row", "Row"), ("cell", "Cell")])
    action = models.CharField(
        max_length=7,
        choices=[
            ("create", "Create"), ("edit", "Edit"),
            ("delete", "Delete"), ("restore", "Restore"),
        ],
    )
    # Historical identity and column labels survive renames/deletion. They are
    # snapshots, not foreign keys, and are never accepted from an API payload.
    actor_id = models.CharField(max_length=64, null=True)
    actor = models.CharField(max_length=255)
    origin = models.CharField(max_length=16, default="write")
    cell_id = models.UUIDField(null=True)
    column_id = models.UUIDField(null=True)
    column_name = models.CharField(max_length=2000, blank=True, default="")
    before_value = models.TextField(null=True)
    after_value = models.TextField(null=True)
    before_truncated = models.BooleanField(default=False)
    after_truncated = models.BooleanField(default=False)

    class Meta:
        ordering = ("-at", "-id")
        indexes = [
            models.Index(fields=["row", "-at", "-id"], name="row_activity_page_idx"),
        ]

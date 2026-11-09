"""Where an imported dataset row came from.

A row imported from Observe keeps one record of its source: the import's
kind (trace, span or session, as the import request named it) and the ids
read from the span the row was made from. Organization and workspace are
copied from the dataset when the record is written, so every read can be
scoped without trusting anything the client sent.
"""

import uuid

from django.db import models


class RowSourceKind(models.TextChoices):
    TRACE = "trace", "Trace"
    SPAN = "span", "Span"
    SESSION = "session", "Session"


class RowSource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    row = models.OneToOneField(
        "model_hub.Row", on_delete=models.CASCADE, related_name="+"
    )
    dataset = models.ForeignKey(
        "model_hub.Dataset", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="+",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=16, choices=RowSourceKind.choices)
    project_id = models.UUIDField()
    trace_id = models.CharField(max_length=255, blank=True, default="")
    span_id = models.CharField(max_length=255)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    # A session import whose span carried no session id: recorded, never guessed.
    session_missing = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "model_hub_row_source"

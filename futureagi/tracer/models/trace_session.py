import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class TraceSession(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sessions",
        blank=False,
        null=False,
    )
    bookmarked = models.BooleanField(default=False)
    name = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"Session {self.id}"

    class Meta:
        db_table = "trace_session"
        ordering = ["-created_at"]

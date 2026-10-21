import uuid

from django.conf import settings
from django.db import models

from tfc.utils.base_model import BaseModel


class SavedSQLQuery(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    sql = models.TextField()
    visibility = models.CharField(
        max_length=12,
        choices=(("private", "Private"), ("project", "Project")),
        default="private",
    )
    chart = models.JSONField(default=dict)
    window_hours = models.PositiveIntegerField(default=24)

    class Meta:
        indexes = [
            models.Index(
                fields=["project", "created_by", "visibility"],
                name="sql_saved_scope_idx",
            )
        ]


class SQLExecution(models.Model):
    """Personal result snapshot; sharing a saved query never shares this record."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sql = models.TextField()
    start = models.DateTimeField()
    end = models.DateTimeField()
    state = models.CharField(
        max_length=12,
        default="queued",
        choices=tuple(
            (v, v) for v in ("queued", "running", "succeeded", "failed", "cancelled")
        ),
    )
    result = models.JSONField(default=dict)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    deadline = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "state", "deadline"], name="sql_admission_idx"
            ),
            models.Index(
                fields=["project", "created_by", "-created_at"], name="sql_history_idx"
            ),
            models.Index(fields=["expires_at"], name="sql_expiry_idx"),
        ]

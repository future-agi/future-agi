"""Remote credentials never live in template JSON or version snapshots."""
import uuid

from django.db import models


class RemoteEvalConfig(models.Model):
    template = models.OneToOneField(
        "model_hub.EvalTemplate", primary_key=True, on_delete=models.CASCADE,
        related_name="remote_credentials",
    )
    encrypted_credentials = models.BinaryField()


class RemoteEvalJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey("model_hub.EvalTemplate", on_delete=models.CASCADE)
    run = models.ForeignKey("model_hub.ExperimentRun", on_delete=models.CASCADE)
    # Freeze settings and credentials at admission, not at worker execution.
    definition = models.JSONField(default=dict)
    encrypted_credentials = models.BinaryField()
    row_ids = models.JSONField(default=list)
    status = models.CharField(max_length=16, default="queued")
    sent = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    result = models.JSONField(default=dict)
    error = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

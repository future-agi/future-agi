"""Versioned metric recipes and committed ClickHouse snapshot manifests."""

import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class DerivedMetric(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.CASCADE)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    current_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True
    )

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                condition=models.Q(deleted=False),
                name="derived_metric_project_name",
            )
        ]


class DerivedMetricVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    metric = models.ForeignKey(
        DerivedMetric, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    formula = models.CharField(max_length=2048)
    inputs = models.JSONField()
    selection_unit = models.CharField(max_length=16)
    weighting = models.CharField(max_length=32)
    cohort_key = models.CharField(max_length=160, blank=True, default="")
    filters = models.JSONField(default=list)
    definition_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["metric", "version"], name="derived_metric_version_unique"
            )
        ]


class DerivedMetricRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(
        DerivedMetricVersion, on_delete=models.PROTECT, related_name="runs"
    )
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True
    )
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    as_of = models.DateTimeField()
    source_run_id = models.CharField(max_length=255, blank=True, default="")
    filters = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64, unique=True)
    snapshot_id = models.UUIDField(null=True)
    result_id = models.UUIDField(null=True)
    attempt_id = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    source_manifest = models.JSONField(null=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    error = models.CharField(max_length=300, blank=True, default="")
    row_count = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(
                fields=["definition", "status", "-window_end"],
                name="derived_run_window_idx",
            )
        ]


class DerivedMetricMonitorDelivery(models.Model):
    """Committed monitor/run evaluations; alert_type is empty for a healthy result."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    monitor = models.ForeignKey(
        "tracer.UserAlertMonitor",
        on_delete=models.CASCADE,
        related_name="derived_metric_deliveries",
    )
    run = models.ForeignKey(
        DerivedMetricRun, on_delete=models.CASCADE, related_name="monitor_deliveries"
    )
    alert_type = models.CharField(max_length=16)
    delivered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["monitor", "run"], name="derived_metric_monitor_run_delivery"
            )
        ]
        indexes = [
            models.Index(
                fields=["monitor", "-delivered_at"], name="derived_monitor_delivery_idx"
            )
        ]

"""Append-only identity bridges, not a second results store.

These models have no implicit tenant manager. Application callers MUST use the
scoped services; results continue to live in ExperimentRun/ExperimentRunItem.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models


class _ImmutableBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Native execution identity cannot be changed.")
        return super().save(*args, **kwargs)


class NativeExperimentSnapshot(_ImmutableBinding):
    experiment = models.OneToOneField(
        "model_hub.ExperimentsTable", on_delete=models.PROTECT,
        related_name="native_snapshot_binding",
    )
    snapshot_dataset = models.OneToOneField(
        "model_hub.Dataset", on_delete=models.PROTECT, related_name="native_snapshot_binding",
    )
    dataset_version = models.ForeignKey(
        "model_hub.DatasetVersion", on_delete=models.PROTECT, related_name="native_snapshots",
    )
    # Column identities captured before execution adds output/evaluation columns.
    column_map = models.JSONField(default=dict)


class NativeExampleBinding(_ImmutableBinding):
    snapshot = models.ForeignKey(
        NativeExperimentSnapshot, on_delete=models.PROTECT, related_name="examples",
    )
    source_row_id = models.UUIDField()
    snapshot_row = models.ForeignKey(
        "model_hub.Row", on_delete=models.PROTECT, related_name="native_example_bindings",
    )
    version_item = models.ForeignKey(
        "model_hub.DatasetVersionItem", on_delete=models.PROTECT,
        related_name="native_example_bindings",
    )
    row_order = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "source_row_id"], name="native_source_row_unique"),
            models.UniqueConstraint(fields=["snapshot", "snapshot_row"], name="native_snapshot_row_unique"),
            models.UniqueConstraint(fields=["snapshot", "version_item"], name="native_version_item_unique"),
        ]


class NativeRunBinding(_ImmutableBinding):
    run = models.OneToOneField(
        "model_hub.ExperimentRun", on_delete=models.PROTECT, related_name="native_binding",
    )
    snapshot = models.ForeignKey(
        NativeExperimentSnapshot, on_delete=models.PROTECT, related_name="runs",
    )
    variant = models.ForeignKey(
        "model_hub.ExperimentDatasetTable", on_delete=models.PROTECT,
        related_name="native_run_bindings",
    )
    execution_id = models.UUIDField()
    native_config_id = models.UUIDField()
    output_column = models.ForeignKey(
        "model_hub.Column", on_delete=models.PROTECT, related_name="native_run_bindings",
    )
    requested_settings = models.JSONField(default=dict)
    effective_settings = models.JSONField(default=dict)
    config = models.JSONField(default=dict)
    request_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["variant", "execution_id"], name="native_variant_execution_unique"),
        ]

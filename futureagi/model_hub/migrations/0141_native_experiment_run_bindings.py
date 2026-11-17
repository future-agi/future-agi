"""Native identity only; deliberately no historical data backfill."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0140_rebackfill_queueitem_project_upgraded")]

    operations = [
        migrations.CreateModel(
            name="NativeExperimentSnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("column_map", models.JSONField(default=dict)),
                ("experiment", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="native_snapshot_binding", to="model_hub.experimentstable")),
                ("snapshot_dataset", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="native_snapshot_binding", to="model_hub.dataset")),
                ("dataset_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="native_snapshots", to="model_hub.datasetversion")),
            ],
        ),
        migrations.CreateModel(
            name="NativeExampleBinding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("source_row_id", models.UUIDField()),
                ("row_order", models.IntegerField()),
                ("snapshot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="examples", to="model_hub.nativeexperimentsnapshot")),
                ("snapshot_row", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="native_example_bindings", to="model_hub.row")),
                ("version_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="native_example_bindings", to="model_hub.datasetversionitem")),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("snapshot", "source_row_id"), name="native_source_row_unique"),
                models.UniqueConstraint(fields=("snapshot", "snapshot_row"), name="native_snapshot_row_unique"),
                models.UniqueConstraint(fields=("snapshot", "version_item"), name="native_version_item_unique"),
            ]},
        ),
        migrations.CreateModel(
            name="NativeRunBinding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("execution_id", models.UUIDField()),
                ("native_config_id", models.UUIDField()),
                ("requested_settings", models.JSONField(default=dict)),
                ("effective_settings", models.JSONField(default=dict)),
                ("config", models.JSONField(default=dict)),
                ("request_hash", models.CharField(max_length=64)),
                ("run", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="native_binding", to="model_hub.experimentrun")),
                ("snapshot", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="runs", to="model_hub.nativeexperimentsnapshot")),
                ("variant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="native_run_bindings", to="model_hub.experimentdatasettable")),
                ("output_column", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="native_run_bindings", to="model_hub.column")),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("variant", "execution_id"), name="native_variant_execution_unique"),
            ]},
        ),
    ]

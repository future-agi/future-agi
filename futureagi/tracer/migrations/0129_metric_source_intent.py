"""Metrics-only journal. Integration with other 0129 leaves needs a merge migration."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0128_base_list_current_stores")]

    operations = [
        migrations.CreateModel(
            name="MetricSourceScope",
            fields=[
                ("epoch", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("organization_id", models.UUIDField()),
                ("project_id", models.UUIDField()),
                ("revision", models.PositiveBigIntegerField(default=0)),
                ("closed_revision", models.PositiveBigIntegerField(default=0)),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("organization_id", "project_id"), name="metric_src_scope"),
                models.CheckConstraint(condition=models.Q(closed_revision__lte=models.F("revision")), name="metric_src_cut_order"),
            ]},
        ),
        migrations.CreateModel(
            name="MetricSourceIntent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operation_key", models.UUIDField()),
                ("revision", models.PositiveBigIntegerField()),
                ("manifest_sha256", models.CharField(max_length=64)),
                ("page_count", models.PositiveSmallIntegerField()),
                ("target_count", models.PositiveSmallIntegerField()),
                ("complete", models.BooleanField(default=False)),
                ("scope", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="tracer.metricsourcescope")),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("scope", "operation_key"), name="metric_src_operation"),
                models.UniqueConstraint(fields=("scope", "revision"), name="metric_src_revision"),
                models.CheckConstraint(condition=models.Q(revision__gt=0), name="metric_src_revision_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="MetricSourcePage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ordinal", models.PositiveSmallIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("payload", models.BinaryField()),
                ("intent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="tracer.metricsourceintent")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("intent", "ordinal"), name="metric_src_page")]},
        ),
        migrations.CreateModel(
            name="MetricSourceDelivery",
            fields=[
                ("receipt_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("server_id", models.UUIDField()),
                ("database", models.CharField(max_length=128)),
                ("store", models.CharField(max_length=64)),
                ("schema_sha256", models.CharField(max_length=64)),
                ("payload_sha256", models.CharField(max_length=64)),
                ("state", models.CharField(choices=[("prepared", "Prepared"), ("uncertain", "Uncertain"), ("acknowledged", "Acknowledged")], default="prepared", max_length=16)),
                ("acknowledgement_sha256", models.CharField(blank=True, max_length=64)),
                ("intent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="tracer.metricsourceintent")),
            ],
            options={"constraints": [
                models.UniqueConstraint(fields=("intent", "server_id", "database", "store"), name="metric_src_target"),
                models.CheckConstraint(
                    condition=(models.Q(state="acknowledged") & ~models.Q(acknowledgement_sha256=""))
                    | (models.Q(state__in=("prepared", "uncertain")) & models.Q(acknowledgement_sha256="")),
                    name="metric_src_ack_state",
                ),
            ]},
        ),
    ]

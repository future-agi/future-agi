import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0080_merge_20260825_1857"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="source_digest",
            field=models.CharField(blank=True, max_length=71, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="bundle_digest",
            field=models.CharField(blank=True, max_length=71, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessjob",
            name="bundle_digest",
            field=models.CharField(blank=True, max_length=71, null=True),
        ),
        migrations.CreateModel(
            name="HostedHarnessStageOutput",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("summary", models.CharField(default="", max_length=1024)),
                ("kind", models.CharField(max_length=64)),
                ("data", models.JSONField()),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stage_outputs",
                        to="simulate.hostedharnessjob",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_hosted_harness_stage_output",
                "indexes": [
                    models.Index(
                        fields=["job", "kind"], name="idx_hstageout_job_kind"
                    ),
                ],
            },
        ),
    ]

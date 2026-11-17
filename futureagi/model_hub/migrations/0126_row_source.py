import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_merge_20260526_0921"),
        ("model_hub", "0125_generated_input_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="RowSource",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("trace", "Trace"),
                            ("span", "Span"),
                            ("session", "Session"),
                        ],
                        max_length=16,
                    ),
                ),
                ("project_id", models.UUIDField()),
                ("trace_id", models.CharField(blank=True, default="", max_length=255)),
                ("span_id", models.CharField(max_length=255)),
                (
                    "session_id",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("session_missing", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="model_hub.dataset",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.organization",
                    ),
                ),
                (
                    "row",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="model_hub.row",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "model_hub_row_source",
            },
        ),
    ]

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0113_merge_actors_and_evidence_import"),
    ]

    operations = [
        migrations.CreateModel(
            name="SimulateEvalConfigRemovedKeys",
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
                ("eval_config_id", models.UUIDField(db_index=True)),
                ("removed", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("restored_at", models.DateTimeField(blank=True, null=True)),
                ("restored_keys", models.JSONField(blank=True, null=True)),
            ],
            options={
                "db_table": "simulate_eval_config_removed_keys",
            },
        ),
    ]

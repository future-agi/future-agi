# Recalculate on an eval-task result records the pair it is replacing, so a
# request killed mid-eval is finished by the eval-task sweep (TH-3119).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0132_merge_alert_webhook_and_sql_workbench"),
    ]

    operations = [
        migrations.CreateModel(
            name="EvalRecalculation",
            fields=[
                (
                    "replacement",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="+",
                        serialize=False,
                        to="tracer.evallogger",
                    ),
                ),
                ("eval_task_id", models.CharField(max_length=255)),
                ("started_at", models.DateTimeField(db_index=True)),
                (
                    "previous",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="tracer.evallogger",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_eval_recalculation",
            },
        ),
    ]

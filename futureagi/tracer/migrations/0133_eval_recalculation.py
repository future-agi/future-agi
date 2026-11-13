# Recalculate records the result it is replacing, so a request killed
# mid-eval is finished by the eval-task sweep (TH-3119).
#
# Lock note: the two foreign keys to tracer_eval_logger are created with the
# table, which takes a SHARE ROW EXCLUSIVE lock on tracer_eval_logger. It is
# brief (the new table is empty), but it queues behind any long transaction on
# tracer_eval_logger and blocks its writes meanwhile: run it in a normal
# deploy window.

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
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
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
                (
                    "replacement",
                    models.OneToOneField(
                        blank=True,
                        null=True,
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

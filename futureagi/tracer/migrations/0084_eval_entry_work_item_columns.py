# atomic=False so the CONCURRENTLY index build/drop run outside a transaction.

import django.contrib.postgres.operations
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("tracer", "0083_merge_20260610_1220"),
    ]

    operations = [
        migrations.AddField(
            model_name="evallogger",
            name="config_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="evallogger",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("errored", "Errored"),
                    ("skipped", "Skipped"),
                ],
                default="completed",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="evaltasklogger",
            name="continuous_cursor",
            field=models.DateTimeField(blank=True, null=True),
        ),
        django.contrib.postgres.operations.AddIndexConcurrently(
            model_name="evallogger",
            index=models.Index(
                fields=["eval_task_id", "status"],
                name="eval_logger_task_status_idx",
            ),
        ),
    ]

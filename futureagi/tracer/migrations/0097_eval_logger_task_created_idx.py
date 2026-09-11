import django.contrib.postgres.operations
from django.db import migrations, models


class Migration(migrations.Migration):
    """Bound newest-first task usage and aggregation reads by time and id."""

    atomic = False

    dependencies = [
        ("tracer", "0096_repair_scanner_cluster_error_count"),
    ]

    operations = [
        django.contrib.postgres.operations.AddIndexConcurrently(
            model_name="evallogger",
            index=models.Index(
                fields=["eval_task_id", "created_at", "id"],
                name="eval_logger_task_created_idx",
                condition=models.Q(eval_task_id__isnull=False, deleted=False),
            ),
        ),
        django.contrib.postgres.operations.AddIndexConcurrently(
            model_name="evallogger",
            index=models.Index(
                fields=[
                    "eval_task_id",
                    "custom_eval_config",
                    "created_at",
                    "id",
                ],
                name="eval_log_task_cfg_created_idx",
                condition=models.Q(eval_task_id__isnull=False, deleted=False),
            ),
        ),
    ]

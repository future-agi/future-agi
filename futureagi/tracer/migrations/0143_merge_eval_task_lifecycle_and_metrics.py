"""Join the eval-task lifecycle fields with dev's Metrics/eval put-back leaf."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0137_evaltask_workflow_generation"),
        ("tracer", "0142_merge_metrics_and_eval_put_back"),
    ]

    operations = []

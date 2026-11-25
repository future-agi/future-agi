"""Learn how many capture statements a project's pass can plan.

One nullable column with no default, index or constraint: existing rows read
as NULL, which keeps the configured METRICS_PASS_MAX_CAPTURES.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0137_metric_version_history_mode")]

    operations = [
        migrations.AddField(
            model_name="metricprojectrefresh",
            name="pass_capture_budget",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]

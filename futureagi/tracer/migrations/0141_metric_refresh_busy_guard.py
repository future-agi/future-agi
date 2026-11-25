"""Add non-identifying busy-slot accounting; existing projects stay unpaused."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0140_metric_refresh_pause")]

    operations = [
        migrations.AddField(
            model_name="metricprojectrefresh",
            name="busy_yields",
            field=models.PositiveIntegerField(default=0, db_default=0),
        ),
        migrations.AddField(
            model_name="metricprojectrefresh",
            name="busy_counts",
            field=models.JSONField(blank=True, default=dict, db_default={}),
        ),
        migrations.AlterField(
            model_name="metricprojectrefresh",
            name="paused_reason",
            field=models.CharField(
                blank=True,
                default="",
                max_length=16,
                choices=[
                    ("", "Not paused"),
                    ("too_large", "Too large"),
                    ("too_busy", "Too busy"),
                ],
            ),
        ),
    ]

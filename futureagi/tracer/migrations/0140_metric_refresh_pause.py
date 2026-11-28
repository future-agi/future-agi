"""Record when a project's metrics pause because the project is too large.

Three columns. paused_reason defaults to "" (not paused), so every existing
project keeps refreshing exactly as before; the other two are nullable.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0139_metric_attempt_rows_collected_at")]

    operations = [
        migrations.AddField(
            model_name="metricprojectrefresh",
            name="paused_reason",
            field=models.CharField(
                blank=True,
                choices=[("", "Not paused"), ("too_large", "Too large")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="metricprojectrefresh",
            name="paused_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="metricprojectrefresh",
            name="paused_rows",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]

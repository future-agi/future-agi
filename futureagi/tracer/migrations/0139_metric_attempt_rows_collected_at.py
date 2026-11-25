"""Record when an unreachable attempt's ClickHouse rows were deleted.

One nullable column with no default, index or constraint: existing rows read
as NULL, so the next maintenance pass collects a closed, unreachable attempt once.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0138_metric_refresh_pass_capture_budget")]

    operations = [
        migrations.AddField(
            model_name="metricrefreshattempt",
            name="rows_collected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

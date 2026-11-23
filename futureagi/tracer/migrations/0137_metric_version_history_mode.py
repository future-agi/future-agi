"""Record per-version history mode for metrics on projects above the size limit.

Both columns are nullable and have no default, index or constraint: existing
rows read as NULL, which keeps today's history-backfill behaviour unchanged.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0136_merge_metrics_and_current_sampling_sql")]

    operations = [
        migrations.AddField(
            model_name="derivedmetricversion",
            name="coverage_start",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="derivedmetricversion",
            name="history_mode",
            field=models.CharField(
                blank=True,
                choices=[("history", "History"), ("forward", "Forward")],
                max_length=16,
                null=True,
            ),
        ),
    ]

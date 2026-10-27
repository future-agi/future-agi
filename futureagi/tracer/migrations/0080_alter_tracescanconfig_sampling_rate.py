from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0079_backfill_skipped_reason"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tracescanconfig",
            name="sampling_rate",
            field=models.FloatField(
                default=0, help_text="0.0-1.0, fraction of traces to scan"
            ),
        ),
    ]

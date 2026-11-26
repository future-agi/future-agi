"""Convert amount_raw and total_usage_raw from BigIntegerField to DecimalField.

Enables fractional usage tracking for ai_credits and voice_sim_minutes dimensions.
Integer dimensions (bytes, requests, tokens, events, hits) store as X.000000 with no
precision loss.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("usage", "0016_merge_0015"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usageeventlog",
            name="amount_raw",
            field=models.DecimalField(
                decimal_places=6,
                help_text="Units consumed in native units (bytes, credits, requests).",
                max_digits=20,
            ),
        ),
        migrations.AlterField(
            model_name="usagesummary",
            name="total_usage_raw",
            field=models.DecimalField(
                decimal_places=6,
                default=0,
                help_text="Cumulative usage in native units (bytes, tokens) for precision.",
                max_digits=20,
            ),
        ),
    ]

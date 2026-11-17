"""Join candidate 7's score value-clock leaf with the 708J round-3 leaf."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0125_backfill_score_value_updated_at"),
        ("model_hub", "0136_merge_708j_round3"),
    ]

    operations = []

"""Join the C7 score value-clock and DSX3e grid snapshot leaves."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0137_merge_dsx3e_grid_snapshot"),
        ("model_hub", "0137_merge_score_value_clock_and_708j_round3"),
    ]

    operations = []

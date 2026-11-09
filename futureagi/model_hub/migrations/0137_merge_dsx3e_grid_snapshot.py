"""Join the T-GRID generated-input snapshot leaf with the 708J round-three leaf."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0125_generated_input_snapshot"),
        ("model_hub", "0136_merge_708j_round3"),
    ]

    operations = []

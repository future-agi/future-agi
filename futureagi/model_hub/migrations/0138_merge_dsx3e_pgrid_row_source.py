"""Join the P-GRID row-source leaf with the T-GRID snapshot join leaf on 708J 3e."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0126_row_source"),
        ("model_hub", "0137_merge_dsx3e_grid_snapshot"),
    ]

    operations = []

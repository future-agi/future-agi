"""Join the DSX3f C7 and P-GRID leaves."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0138_merge_dsx3e_c7_and_grid_snapshot"),
        ("model_hub", "0138_merge_dsx3e_pgrid_row_source"),
    ]

    operations = []

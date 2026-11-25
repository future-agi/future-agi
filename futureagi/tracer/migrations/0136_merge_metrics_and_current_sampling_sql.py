"""Join derived metrics with the current SQL workbench and sampling stack."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0132_merge_sampling_and_sql_workbench_dev_merge"),
        ("tracer", "0135_merge_metrics_b1_assembly"),
    ]

    operations = []

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0117_sampling_project_db_default"),
        ("tracer", "0120_derived_metric_delivery_ledger"),
    ]

    operations = []

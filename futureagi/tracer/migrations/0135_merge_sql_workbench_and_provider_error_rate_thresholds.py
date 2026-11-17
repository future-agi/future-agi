from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0131_merge_sql_workbench_and_observationspan_internal_type"),
        ("tracer", "0134_convert_provider_error_rate_thresholds"),
    ]

    operations = []

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0135_merge_sql_workbench_and_provider_error_rate_thresholds"),
        ("tracer", "0133_merge_sampling_and_alert_webhook"),
    ]

    operations = []

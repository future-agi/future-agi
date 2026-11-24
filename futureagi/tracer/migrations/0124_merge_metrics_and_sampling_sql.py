from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0121_merge_sampling_and_sql_reuse"),
        ("tracer", "0123_derived_metric_confident_alert"),
    ]

    operations = []

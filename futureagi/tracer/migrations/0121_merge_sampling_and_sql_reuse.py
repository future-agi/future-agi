from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0119_sql_execution_result_reuse"),
        ("tracer", "0120_sampling_cohort_lease"),
    ]

    operations = []

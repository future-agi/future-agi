from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0129_metric_compaction_range"),
        ("tracer", "0129_metric_source_intent"),
        ("tracer", "0129_metric_pg_response_revisions"),
        ("tracer", "0129_user_activity_store"),
    ]

    operations = []

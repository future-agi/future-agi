from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0118_backfill_feed_issue_statuses"),
        ("tracer", "0130_observationspan_internal_type"),
    ]

    operations = []

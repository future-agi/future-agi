"""Join the existing assembly and B1 metric migration branches."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0131_metric_range_user_activity_choice"),
        ("tracer", "0134_metric_compaction_user_activity_constraint"),
    ]

    operations = []

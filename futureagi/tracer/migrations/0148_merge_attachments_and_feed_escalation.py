"""Join the attachment and Error Feed escalation migration histories."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0143_merge_feed_escalation_and_metrics"),
        ("tracer", "0147_merge_attachments_and_metrics"),
    ]

    operations = []

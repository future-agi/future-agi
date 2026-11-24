"""Join the Error Feed escalation and Metrics migration histories."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0131_merge_feed_escalation_and_internal_span_type"),
        ("tracer", "0142_merge_metrics_and_eval_put_back"),
    ]

    operations = []

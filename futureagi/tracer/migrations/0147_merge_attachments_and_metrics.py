"""Join the attachment and derived metrics migration histories."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0142_merge_metrics_and_eval_put_back"),
        ("tracer", "0146_attachment_derivative_dispatch_index"),
    ]

    operations = []

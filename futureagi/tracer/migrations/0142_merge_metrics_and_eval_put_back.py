"""Join the Metrics refresh guard and eval put-back migration histories."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0135_eval_put_back"),
        ("tracer", "0141_metric_refresh_busy_guard"),
    ]

    operations = []

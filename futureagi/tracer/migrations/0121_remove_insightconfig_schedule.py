from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0120_merge_error_feed_alerts_and_instant_eval"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="insightconfig",
            name="schedule",
        ),
    ]

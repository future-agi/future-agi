from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0136_merge_line_and_dev_1fc7d403"),
    ]

    operations = [
        migrations.AddField(
            model_name="useralertmonitor",
            name="pagerduty_routing_key",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]

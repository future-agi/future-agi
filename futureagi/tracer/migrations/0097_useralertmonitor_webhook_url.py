from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0096_repair_scanner_cluster_error_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="useralertmonitor",
            name="webhook_url",
            field=models.URLField(blank=True, null=True),
        ),
    ]

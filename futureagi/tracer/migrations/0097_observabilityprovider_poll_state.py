from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0096_repair_scanner_cluster_error_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="observabilityprovider",
            name="poll_state",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]

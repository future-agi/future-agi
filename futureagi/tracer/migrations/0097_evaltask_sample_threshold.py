from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0096_repair_scanner_cluster_error_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="sample_threshold",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]

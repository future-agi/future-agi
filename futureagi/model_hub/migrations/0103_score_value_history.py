from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0102_canonicalize_performance_report_filters"),
    ]

    operations = [
        migrations.AddField(
            model_name="score",
            name="value_history",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

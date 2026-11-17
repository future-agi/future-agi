from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0139_export_schedule")]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="webhook_url",
            field=models.URLField(blank=True, default="", max_length=2048),
        ),
        migrations.AddField(
            model_name="evaltask",
            name="webhook_condition",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]

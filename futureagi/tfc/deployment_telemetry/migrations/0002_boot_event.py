from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("deployment_telemetry", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="deploymenttelemetrystate",
            name="boot_event_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

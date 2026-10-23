from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0122_merge_metrics_and_simulation_project")]

    operations = [
        migrations.AddField(
            model_name="useralertmonitor",
            name="alert_when",
            field=models.CharField(
                choices=[
                    ("point", "Point estimate"),
                    ("confident", "Whole 95% interval"),
                ],
                default="point",
                max_length=16,
            ),
        ),
    ]

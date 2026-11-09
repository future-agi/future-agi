from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0124_experiment_run_control")]
    operations = [
        migrations.AddField(
            model_name="experimentruncontrol", name="phase",
            field=models.CharField(default="RUNNING", max_length=16),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0124_experiment_run_scope")]

    operations = [
        migrations.AddField(
            model_name="experimentstable",
            name="run_policy",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

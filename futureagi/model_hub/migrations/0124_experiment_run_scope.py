from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0123_usereval_is_experiment_default")]

    operations = [
        migrations.AddField(
            model_name="experimentstable",
            name="run_scope",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="experimentstable",
            name="run_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

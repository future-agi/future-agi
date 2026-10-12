from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0097_merge_selected_runs_environment_v3")]

    operations = [
        migrations.AddField(
            model_name="callexecution",
            name="audio_metrics",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="callexecution",
            name="audio_provenance",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="callexecution",
            name="audio_analysis_generation",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="callexecutionsnapshot",
            name="audio_metrics",
            field=models.JSONField(blank=True, null=True),
        ),
    ]

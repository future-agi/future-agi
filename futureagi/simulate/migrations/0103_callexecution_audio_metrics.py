from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0102_agent_fix_engine_notes")]

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
            # db_default keeps the add metadata-only on Postgres (no rewrite,
            # no backend CHECK) for this high-volume table.
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="callexecutionsnapshot",
            name="audio_metrics",
            field=models.JSONField(blank=True, null=True),
        ),
    ]

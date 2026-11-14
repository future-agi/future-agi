from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0127_project_analysis_source_generation")]

    operations = [
        migrations.AddField(
            model_name="instantevalrow",
            name="source_session_ids",
            field=models.JSONField(blank=True, editable=False, null=True),
        ),
    ]

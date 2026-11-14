from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0125_insightrun_source_started_at")]

    operations = [
        migrations.RemoveIndex(
            model_name="insightrun", name="insight_run_org_source_idx"
        ),
        migrations.RenameField(
            model_name="insightrun",
            old_name="source_started_at",
            new_name="source_created_at",
        ),
        migrations.AddIndex(
            model_name="insightrun",
            index=models.Index(
                fields=["organization", "source_created_at"],
                name="insight_run_org_source_idx",
            ),
        ),
        migrations.AddField(
            model_name="instantevalrow",
            name="source_created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="instantevalrow",
            index=models.Index(
                fields=["source_created_at", "task"], name="instant_row_source_idx"
            ),
        ),
    ]

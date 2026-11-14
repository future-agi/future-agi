from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0124_merge_analysis_and_alert_high_water"),
    ]

    operations = [
        migrations.AddField(
            model_name="insightrun",
            name="source_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="insightrun",
            index=models.Index(
                fields=["organization", "source_started_at"],
                name="insight_run_org_source_idx",
            ),
        ),
    ]

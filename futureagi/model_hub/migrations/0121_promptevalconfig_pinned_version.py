import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0120_backfill_score_tracer_project"),
    ]

    operations = [
        migrations.AddField(
            model_name="promptevalconfig",
            name="pinned_version",
            field=models.ForeignKey(
                blank=True,
                help_text="Pin to a specific eval template version for workbench runtime.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_prompt_eval_configs",
                to="model_hub.evaltemplateversion",
            ),
        ),
    ]

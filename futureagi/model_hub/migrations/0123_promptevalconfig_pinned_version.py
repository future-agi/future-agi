import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0122_backfill_queueitem_source_preview"),
    ]

    operations = [
        migrations.AddField(
            model_name="promptevalconfig",
            name="pinned_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_prompt_eval_configs",
                to="model_hub.evaltemplateversion",
            ),
        ),
    ]

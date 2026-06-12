import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0083_merge_20260610_1220"),
        ("model_hub", "0110_merge_20260609_1253"),
    ]

    operations = [
        migrations.AddField(
            model_name="customevalconfig",
            name="pinned_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_custom_eval_configs",
                to="model_hub.evaltemplateversion",
                help_text="Pin to a specific template version for runtime.",
            ),
        ),
    ]

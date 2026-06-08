import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0082_decouple_trace_session_fk"),
        ("model_hub", "0106_decouple_telemetry_fk_constraints"),
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

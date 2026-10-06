import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0122_backfill_queueitem_source_preview"),
        ("simulate", "0084_expand_hosted_harness_scenario_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="dataset_row",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The exact row represented by this hosted scenario key. Multiple "
                    "registrations can share one dataset-backed scenario."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="hosted_registrations",
                to="model_hub.row",
            ),
        ),
    ]

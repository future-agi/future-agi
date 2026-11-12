# DSX launch L5: dataset evals preselected in new experiments.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0122_backfill_queueitem_source_preview"),
    ]

    operations = [
        migrations.AddField(
            model_name="userevalmetric",
            name="is_experiment_default",
            field=models.BooleanField(
                default=False,
                help_text="Preselect this dataset eval in the dataset's new experiments.",
            ),
        ),
    ]

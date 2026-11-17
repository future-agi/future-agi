"""Join the accepted dataset and integration leaves for 708J round 3."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0126_dataset_folders"),
        ("model_hub", "0128_merge_scope_and_splits"),
        ("model_hub", "0135_evaltemplate_eval_type_external_and_dag"),
    ]

    operations = []

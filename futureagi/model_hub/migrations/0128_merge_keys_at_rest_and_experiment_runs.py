from django.db import migrations


class Migration(migrations.Migration):
    # Integration round 7: the gateway release's model-hub keys at rest
    # (0124-0125, RF19) and DSX's dataset versions and experiment runs
    # (0123-0127) are separate leaves.
    dependencies = [
        ("model_hub", "0125_encrypt_model_hub_keys_at_rest"),
        ("model_hub", "0127_experimentrun_ordinal_datasetbaseline"),
    ]

    operations = []

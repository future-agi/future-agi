from django.db import migrations


class Migration(migrations.Migration):
    # Round 7B combines the webhook label-head table with the existing
    # experiment-run and encrypted-key history. Both branches must run.
    dependencies = [
        ("model_hub", "0123_promptlabelhead"),
        ("model_hub", "0128_merge_keys_at_rest_and_experiment_runs"),
    ]

    operations = []

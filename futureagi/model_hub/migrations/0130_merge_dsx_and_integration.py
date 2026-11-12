from django.db import migrations


class Migration(migrations.Migration):
    # FA dev integration (ruling 708): DSX's dataset default-eval flag
    # (0123_usereval_is_experiment_default, launch L5) and the integration
    # history through 0129 (keys at rest, experiment runs, prompt label
    # heads) are separate leaves. Both branches must run; nothing else changes.
    dependencies = [
        ("model_hub", "0123_usereval_is_experiment_default"),
        ("model_hub", "0129_merge_prompt_label_heads_and_experiment_runs"),
    ]

    operations = []

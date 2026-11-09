from django.db import migrations


class Migration(migrations.Migration):
    # The dataset works line (run control, stars, descriptions and tags,
    # decision-tree evals; through 0127) and the integration line (keys at
    # rest, experiment runs, external evals; through 0130) are separate
    # leaves. Both branches run; this changes nothing.
    dependencies = [
        ("model_hub", "0127_merge_dsx_works_705_r4"),
        ("model_hub", "0130_merge_dsx_and_integration"),
    ]

    operations = []

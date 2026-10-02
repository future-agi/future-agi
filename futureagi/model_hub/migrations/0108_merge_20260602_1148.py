from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0106_decouple_telemetry_fk_constraints"),
        ("model_hub", "0107_customaimodel_baseline_default_metric"),
    ]

    operations = []

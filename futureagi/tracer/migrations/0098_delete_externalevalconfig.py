# Generated for #2519 — retire ExternalEvalConfig after the external-eval-config path.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0097_eval_logger_task_created_idx"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ExternalEvalConfig",
        ),
    ]

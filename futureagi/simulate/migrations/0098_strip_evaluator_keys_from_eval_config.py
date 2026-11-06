"""Run the evaluator-key strip on every simulate eval attachment once.
The command ``strip_eval_config_evaluator_keys`` re-runs it after a rollout."""

from django.db import migrations

from simulate.services.eval_config_repair import strip_evaluator_keys


def forwards(apps, schema_editor):
    strip_evaluator_keys(apps.get_model("simulate", "SimulateEvalConfig"))


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0097_merge_selected_runs_environment_v3"),
    ]

    operations = [
        # Nothing to put back: grading wrote these values, not a user, and the next
        # grade derives them from the template again.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

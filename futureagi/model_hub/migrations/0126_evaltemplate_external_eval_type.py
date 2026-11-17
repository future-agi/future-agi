from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the ``external`` evaluator type (SRV-5).

    Choices are not enforced by the database, so this changes no rows and no
    schema; it keeps the migration state in step with the model.
    """

    dependencies = [
        ("model_hub", "0125_experiment_run_mvp"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evaltemplate",
            name="eval_type",
            field=models.CharField(
                choices=[
                    ("agent", "Agent"),
                    ("llm", "LLM"),
                    ("code", "Code"),
                    ("external", "External"),
                ],
                default="llm",
                help_text=(
                    "Evaluator type: agent (Falcon AI powered), llm (LLM-as-a-judge), "
                    "code (custom code), external (scored by the caller, e.g. the SDK; "
                    "never executed by the server)"
                ),
                max_length=10,
            ),
        ),
    ]

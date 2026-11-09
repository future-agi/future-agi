from django.db import migrations, models


class Migration(migrations.Migration):
    """Keep the migration state of EvalTemplate.eval_type in step with the model.

    The two merged lines each added one evaluator type (dag, external) with its
    own AlterField. Choices are not enforced by the database, so this changes no
    rows and no schema.
    """

    dependencies = [
        ("model_hub", "0134_merge_dataset_works_and_integration"),
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
                    ("dag", "Decision tree"),
                ],
                default="llm",
                help_text=(
                    "Evaluator type: agent (Falcon AI powered), llm (LLM-as-a-judge), "
                    "code (custom code), dag (decision tree), external (scored by the "
                    "caller, e.g. the SDK; never executed by the server)"
                ),
                max_length=10,
            ),
        ),
    ]

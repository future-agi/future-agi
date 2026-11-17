# Decision-tree evals are their own evaluator type.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0123_usereval_is_experiment_default"),
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
                    ("dag", "Decision tree"),
                ],
                default="llm",
                help_text="Evaluator type: agent (Falcon AI powered), llm (LLM-as-a-judge), code (custom code), dag (decision tree)",
                max_length=10,
            ),
        ),
    ]

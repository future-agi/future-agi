from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0129_project_analysis_source_generation_db_default")]

    operations = [
        migrations.AlterField(
            model_name="observationspan",
            name="observation_type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("tool", "Tool"),
                    ("chain", "Chain"),
                    ("llm", "LLM"),
                    ("retriever", "Retriever"),
                    ("embedding", "Embedding"),
                    ("agent", "Agent"),
                    ("reranker", "Reranker"),
                    ("unknown", "Unknown"),
                    ("guardrail", "Guardrail"),
                    ("evaluator", "Evaluator"),
                    ("conversation", "Conversation"),
                    ("internal", "Internal"),
                ],
            ),
        ),
    ]

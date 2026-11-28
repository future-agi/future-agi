from django.db import migrations, models

# Adds a nullable column to the registered high-volume eval-task table.
UPDATE_SCOPE = ("postgres:tracer_eval_task",)


class Migration(migrations.Migration):
    dependencies = [("tracer", "0143_merge_feed_escalation_and_metrics")]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="failure_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("READ_BUDGET", "READ_BUDGET"),
                    ("ROW_LIMIT", "ROW_LIMIT"),
                    ("AMBIGUOUS_SPAN", "AMBIGUOUS_SPAN"),
                    ("UNSUPPORTED_FILTER", "UNSUPPORTED_FILTER"),
                    ("ANNOTATION_METADATA", "ANNOTATION_METADATA"),
                    ("EVAL_METADATA", "EVAL_METADATA"),
                    ("SELECTOR_ERROR", "SELECTOR_ERROR"),
                ],
                max_length=32,
                null=True,
            ),
        ),
    ]

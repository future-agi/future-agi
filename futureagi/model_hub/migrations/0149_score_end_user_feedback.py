from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0148_remote_evaluators"),
    ]

    operations = [
        migrations.AlterField(
            model_name="score",
            name="score_source",
            field=models.CharField(
                choices=[
                    ("human", "Human"),
                    ("api", "Api"),
                    ("auto", "Auto"),
                    ("imported", "Imported"),
                    ("end_user", "End User"),
                ],
                default="human",
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="score",
            name="unique_score_trace_label_null_annotator",
        ),
        migrations.RemoveConstraint(
            model_name="score",
            name="unique_score_span_label_null_annotator",
        ),
        migrations.AddConstraint(
            model_name="score",
            constraint=models.UniqueConstraint(
                fields=("trace", "label"),
                condition=models.Q(
                    deleted=False, trace__isnull=False, annotator__isnull=True
                ) & ~models.Q(score_source="end_user"),
                name="unique_score_trace_label_null_annotator",
            ),
        ),
        migrations.AddConstraint(
            model_name="score",
            constraint=models.UniqueConstraint(
                fields=("observation_span", "label"),
                condition=models.Q(
                    deleted=False, observation_span__isnull=False, annotator__isnull=True
                ) & ~models.Q(score_source="end_user"),
                name="unique_score_span_label_null_annotator",
            ),
        ),
    ]

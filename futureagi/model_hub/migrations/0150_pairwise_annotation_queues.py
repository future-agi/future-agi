import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0149_score_end_user_feedback"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="annotationqueue",
            name="mode",
            field=models.CharField(
                choices=[("standard", "Standard"), ("pairwise", "Pairwise")],
                default="standard", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="queueitem", name="source_a",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="queueitem", name="source_b",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="queueitem", name="source_type",
            field=models.CharField(
                choices=[
                    ("dataset_row", "Dataset Row"), ("trace", "Trace"),
                    ("observation_span", "Observation Span"),
                    ("prototype_run", "Prototype Run"),
                    ("call_execution", "Call Execution"),
                    ("trace_session", "Trace Session"), ("pairwise", "Pairwise"),
                ],
                max_length=50,
            ),
        ),
        migrations.CreateModel(
            name="PairwiseAnnotation",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("choice", models.CharField(choices=[("A", "A"), ("B", "B"), ("tie", "Tie")], max_length=3)),
                ("comment", models.TextField(blank=True, default="")),
                ("annotator", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="pairwise_annotations", to=settings.AUTH_USER_MODEL,
                )),
                ("queue_item", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="pairwise_annotations", to="model_hub.queueitem",
                )),
                ("organization", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="pairwise_annotations", to="accounts.organization",
                )),
                ("workspace", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="pairwise_annotations", to="accounts.workspace",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("queue_item", "annotator"), condition=models.Q(deleted=False),
                        name="unique_active_pairwise_vote",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(choice__in=["A", "B", "tie"]),
                        name="valid_pairwise_vote_choice",
                    ),
                ],
            },
        ),
    ]

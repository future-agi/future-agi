import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("model_hub", "0146_dataset_version_tags"),
    ]

    operations = [
        migrations.CreateModel(
            name="PairwisePreference",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "row_id",
                    models.UUIDField(
                        help_text="Stable dataset-version example_id.",
                    ),
                ),
                (
                    "winner",
                    models.CharField(
                        choices=[("a", "A"), ("b", "B"), ("tie", "Tie")],
                        max_length=3,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[("human", "Human"), ("judge", "Judge")],
                        default="human",
                        max_length=5,
                    ),
                ),
                ("note", models.TextField(blank=True, default="", max_length=4000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "experiment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="model_hub.experimentstable",
                    ),
                ),
                (
                    "run_a",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="model_hub.experimentrun",
                    ),
                ),
                (
                    "run_b",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="model_hub.experimentrun",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "experiment",
                            "run_a",
                            "run_b",
                            "row_id",
                            "created_by",
                        ),
                        name="pairwise_preference_reviewer_unique",
                    ),
                ],
            },
        ),
    ]

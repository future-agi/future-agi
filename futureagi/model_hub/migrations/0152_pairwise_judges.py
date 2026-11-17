import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("model_hub", "0151_experiment_summary_evaluators"),
    ]

    operations = [
        migrations.CreateModel(
            name="PairwiseJudgeJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                ("status", models.CharField(max_length=12, default="queued")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(null=True, blank=True)),
                (
                    "experiment",
                    models.ForeignKey(
                        to="model_hub.experimentstable",
                        on_delete=models.CASCADE,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        to="accounts.organization",
                        on_delete=models.PROTECT,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        to="accounts.workspace",
                        on_delete=models.PROTECT,
                    ),
                ),
                (
                    "run_a",
                    models.ForeignKey(
                        to="model_hub.experimentrun",
                        on_delete=models.CASCADE,
                        related_name="+",
                    ),
                ),
                (
                    "run_b",
                    models.ForeignKey(
                        to="model_hub.experimentrun",
                        on_delete=models.CASCADE,
                        related_name="+",
                    ),
                ),
                (
                    "dataset_version",
                    models.ForeignKey(
                        to="model_hub.datasetversion",
                        on_delete=models.PROTECT,
                    ),
                ),
                (
                    "judge_version",
                    models.ForeignKey(
                        to="model_hub.evaltemplateversion",
                        on_delete=models.PROTECT,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        to=settings.AUTH_USER_MODEL,
                        on_delete=models.PROTECT,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["run_a", "run_b", "judge_version"],
                        name="pairwise_judge_pair_version_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(run_a__lt=models.F("run_b")),
                        name="pairwise_judge_canonical_pair",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PairwiseJudgeItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        to="model_hub.pairwisejudgejob",
                        on_delete=models.CASCADE,
                        related_name="items",
                    ),
                ),
                ("example_id", models.UUIDField()),
                ("trial_number", models.PositiveIntegerField(default=1)),
                ("snapshot", models.JSONField()),
                (
                    "presentation_order",
                    models.CharField(
                        max_length=2,
                        choices=[("ab", "AB"), ("ba", "BA")],
                    ),
                ),
                ("status", models.CharField(max_length=12, default="queued")),
                (
                    "winner",
                    models.CharField(
                        max_length=3,
                        blank=True,
                        default="",
                        choices=[("a", "A"), ("b", "B"), ("tie", "Tie")],
                    ),
                ),
                ("reason", models.TextField(blank=True, default="")),
                ("error", models.CharField(max_length=40, blank=True, default="")),
                ("log_id", models.CharField(max_length=255, blank=True, default="")),
                ("lease_until", models.DateTimeField(null=True, blank=True)),
                ("finished_at", models.DateTimeField(null=True, blank=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["job", "example_id", "trial_number"],
                        name="pairwise_judge_item_unique",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["job", "status"],
                        name="pairwise_judge_state",
                    ),
                ],
            },
        ),
    ]

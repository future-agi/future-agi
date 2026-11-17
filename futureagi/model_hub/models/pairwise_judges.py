"""Automated verdicts never share a human reviewer's uniqueness key."""

import uuid

from django.db import models


class PairwiseJudgeJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey("ExperimentsTable", on_delete=models.CASCADE)
    organization = models.ForeignKey("accounts.Organization", models.PROTECT)
    workspace = models.ForeignKey("accounts.Workspace", models.PROTECT)
    run_a = models.ForeignKey("ExperimentRun", models.CASCADE, related_name="+")
    run_b = models.ForeignKey("ExperimentRun", models.CASCADE, related_name="+")
    dataset_version = models.ForeignKey("DatasetVersion", models.PROTECT)
    judge_version = models.ForeignKey("EvalTemplateVersion", models.PROTECT)
    created_by = models.ForeignKey("accounts.User", models.PROTECT)
    status = models.CharField(max_length=12, default="queued")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run_a", "run_b", "judge_version"],
                name="pairwise_judge_pair_version_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(run_a__lt=models.F("run_b")),
                name="pairwise_judge_canonical_pair",
            ),
        ]


class PairwiseJudgeItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(PairwiseJudgeJob, models.CASCADE, related_name="items")
    example_id = models.UUIDField()
    trial_number = models.PositiveIntegerField(default=1)
    snapshot = models.JSONField()
    presentation_order = models.CharField(
        max_length=2, choices=[("ab", "AB"), ("ba", "BA")]
    )
    status = models.CharField(max_length=12, default="queued")
    winner = models.CharField(
        max_length=3,
        blank=True,
        default="",
        choices=[("a", "A"), ("b", "B"), ("tie", "Tie")],
    )
    reason = models.TextField(blank=True, default="")
    error = models.CharField(max_length=40, blank=True, default="")
    log_id = models.CharField(max_length=255, blank=True, default="")
    lease_until = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "example_id", "trial_number"],
                name="pairwise_judge_item_unique",
            ),
        ]
        indexes = [models.Index(fields=["job", "status"], name="pairwise_judge_state")]

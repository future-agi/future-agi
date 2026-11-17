import uuid

from django.db import models


class PairwisePreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey("ExperimentsTable", on_delete=models.CASCADE)
    run_a = models.ForeignKey("ExperimentRun", models.CASCADE, related_name="+")
    run_b = models.ForeignKey("ExperimentRun", models.CASCADE, related_name="+")
    row_id = models.UUIDField(help_text="Stable dataset-version example_id.")
    winner = models.CharField(
        max_length=3,
        choices=[("a", "A"), ("b", "B"), ("tie", "Tie")],
    )
    source = models.CharField(
        max_length=5,
        choices=[("human", "Human"), ("judge", "Judge")],
        default="human",
    )
    note = models.TextField(blank=True, default="", max_length=4000)
    created_by = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "run_a", "run_b", "row_id", "created_by"],
                name="pairwise_preference_reviewer_unique",
            ),
        ]

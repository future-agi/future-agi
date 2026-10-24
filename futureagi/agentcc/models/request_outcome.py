from django.db import models

from accounts.models import Organization


class AgentccRequestOutcome(models.Model):
    """Whether a gateway request did its job, for cost per successful task.

    ``classified`` rows come from the gateway's ``outcome_class`` log metadata,
    one per request. ``feedback`` and ``prev_outcome`` rows come from
    ``outcome-event-v1`` records and are deduplicated on ``event_id``. No
    request or response content is stored.
    """

    class Outcome(models.TextChoices):
        OK = "ok"
        BAD = "bad"
        EMPTY = "empty"
        CONTENT_FILTER = "content_filter"
        VERIFY_FAILED = "verify_failed"
        RETRIED = "retried"

    class Source(models.TextChoices):
        CLASSIFIED = "classified"
        FEEDBACK = "feedback"
        PREV_OUTCOME = "prev_outcome"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_request_outcomes",
        db_index=False,  # covered by the (organization, ...) indexes below
    )
    request_id = models.CharField(max_length=64)
    agent_run_id = models.CharField(max_length=128, null=True, blank=True)
    agent_id = models.CharField(max_length=128, null=True, blank=True)
    agent_step = models.CharField(max_length=32, null=True, blank=True)
    model = models.CharField(max_length=128, blank=True, default="")
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    source = models.CharField(max_length=16, choices=Source.choices)
    score = models.FloatField(null=True, blank=True)
    # Null for classified rows, which are unique per request instead.
    event_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "agentcc_request_outcome"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request_id", "source"],
                condition=models.Q(source="classified"),
                name="agentcc_outcome_one_classified",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "request_id"],
                name="agentcc_out_org_request_idx",
            ),
            models.Index(
                fields=["organization", "agent_run_id"],
                name="agentcc_out_org_run_idx",
            ),
            # Retention purges outcomes by age.
            models.Index(
                fields=["organization", "occurred_at"],
                name="agentcc_out_org_occurred_idx",
            ),
        ]

    def __str__(self):
        return f"{self.request_id} {self.source}:{self.outcome}"

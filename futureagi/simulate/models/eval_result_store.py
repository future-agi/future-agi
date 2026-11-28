"""Where results of grading saved calls live, apart from the run's own score.

A call's ``eval_outputs`` slot is the run's own score: the run's pass or fail,
its totals and any baseline read it. Nothing here writes that slot.

``SimulateEvalResult`` is one call and one question. Today every row is a
verdict of the run's own that grading again replaced, kept so it is never
lost. The table is named for results, not for replaced verdicts only, because
the answers of a later "ask" (a person's questions over saved calls) are rows
here too; the change that writes them adds their columns, all nullable.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import CallExecution
from tfc.utils.base_model import BaseModel


class SimulateEvalResult(BaseModel):
    """One call, one question: a replaced verdict of the run's own."""

    class Origin(models.TextChoices):
        # The only origin for now. It is a column already so rows are labelled
        # when other origins arrive, and nothing is backfilled then.
        REPLACED_OWN_SCORE = "replaced_own_score", "The run's own score, replaced"

    class State(models.TextChoices):
        ANSWERED = "answered", "Answered"
        UNMEASURED = "unmeasured", "Unmeasured"
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        GRADING_ERROR = "grading_error", "Grading error"
        INFRASTRUCTURE_ERROR = "infrastructure_error", "Infrastructure error"
        CANCELLED = "cancelled", "Cancelled"
        PENDING = "pending", "Pending"
        # Only on a replaced verdict whose stored shape says none of the above.
        UNMAPPED = "unmapped", "Not mapped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    origin = models.CharField(max_length=32, choices=Origin.choices)
    # On a replaced verdict: one id per request that replaced verdicts, so a
    # request whose old slots are put back finds exactly its own rows.
    replacement_id = models.UUIDField(null=True, blank=True)
    # Results go with their call, and so with their run.
    call_execution = models.ForeignKey(
        CallExecution, on_delete=models.CASCADE, related_name="eval_results"
    )
    # The configured eval, while it exists. ``question_key`` and
    # ``question_definition`` keep the row readable after it is removed.
    eval_config = models.ForeignKey(
        SimulateEvalConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    question_key = models.CharField(max_length=255)
    question_definition = models.JSONField(null=True, blank=True)
    state = models.CharField(max_length=32, choices=State.choices)
    cause = models.CharField(max_length=64, blank=True, default="")
    label = models.TextField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    # On a replaced verdict: the slot exactly as it was, and when and by whom
    # it was replaced. Nobody is named when a job, not a person, regraded it.
    replaced_verdict = models.JSONField(null=True, blank=True)
    replaced_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_eval_result"
        constraints = [
            # A replaced verdict names the request that replaced it, and when.
            # The columns stay nullable and this check requires them, so a
            # later origin widens the check and loosens no column.
            models.CheckConstraint(
                condition=models.Q(
                    origin="replaced_own_score",
                    replaced_at__isnull=False,
                    replacement_id__isnull=False,
                ),
                name="eval_result_replaced_names_its_request",
            ),
            models.UniqueConstraint(
                fields=["replacement_id", "call_execution", "question_key"],
                condition=models.Q(origin="replaced_own_score"),
                name="uniq_eval_result_replacement_call_question",
            ),
        ]

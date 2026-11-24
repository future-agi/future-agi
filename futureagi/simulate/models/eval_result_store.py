"""Where results of grading saved calls live, apart from the run's own score.

A call's ``eval_outputs`` slot is the run's own score: the run's pass or fail,
its totals and any baseline read it. Nothing here writes that slot.

- ``SimulateEvalAsk`` is one launch by a person: a frozen list of calls and the
  questions asked of them.
- ``SimulateEvalResult`` is one call and one question. It is either the answer of an
  ask, or a verdict of the run's own that ``run-new-evals`` replaced, kept so
  grading again never loses the original.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.test_execution import CallExecution
from tfc.utils.base_model import BaseModel


class SimulateEvalAsk(BaseModel):
    """One launch by a person, over a frozen selection of finished calls."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # The calls, in order, as ids, and how the user described the selection
    # (its kind, the runs, the filter as asked). Never worked out again.
    selection = models.JSONField()
    # Each question: its key, its definition, its version and the kinds it reads.
    questions = models.JSONField()
    # Per kind and cause, how many calls were ready and how many unmeasured,
    # as shown before launch.
    counts_shown = models.JSONField(null=True, blank=True)
    # The cost shown to the person and accepted.
    cost_shown = models.JSONField(null=True, blank=True)
    engine = models.CharField(max_length=64, blank=True, default="")
    engine_run_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        db_table = "simulate_eval_ask"
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="idx_eval_ask_org_created",
            ),
        ]


class SimulateEvalResult(BaseModel):
    """One call, one question: an ask's answer, or a replaced verdict of the run's own."""

    class Origin(models.TextChoices):
        ASK = "ask", "Answer to an ask"
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
    ask = models.ForeignKey(
        SimulateEvalAsk,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="results",
    )
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
    # A hash of what decided the answer. Empty on a replaced verdict: the slot
    # it came from records no version.
    question_version = models.CharField(max_length=128, blank=True, default="")
    question_definition = models.JSONField(null=True, blank=True)
    state = models.CharField(max_length=32, choices=State.choices)
    cause = models.CharField(max_length=64, blank=True, default="")
    label = models.TextField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    reason = models.TextField(null=True, blank=True)
    # For each kind read: its source, a digest of the exact content, any cut or mark.
    evidence = models.JSONField(null=True, blank=True)
    # The evaluator's own log row, which carries the template version and the charge.
    evaluator_log_id = models.UUIDField(null=True, blank=True)
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
            # An ask's answers name their ask; a replaced verdict names none,
            # and names the request that replaced it instead.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin="ask", ask__isnull=False, replacement_id__isnull=True
                    )
                    | models.Q(
                        origin="replaced_own_score",
                        ask__isnull=True,
                        replacement_id__isnull=False,
                    )
                ),
                name="eval_result_origin_matches_ask",
            ),
            models.UniqueConstraint(
                fields=["replacement_id", "call_execution", "question_key"],
                condition=models.Q(origin="replaced_own_score"),
                name="uniq_eval_result_replacement_call_question",
            ),
            # One answer per call and question in an ask, so a task delivered
            # twice leaves one row.
            models.UniqueConstraint(
                fields=["ask", "call_execution", "question_key"],
                condition=models.Q(origin="ask"),
                name="uniq_eval_result_ask_call_question",
            ),
        ]
        indexes = [
            models.Index(
                fields=["call_execution", "question_key"],
                name="idx_eval_result_call_question",
            ),
        ]

"""An ask: one launch, by a person, of questions over saved calls they chose.

Its answers are ``SimulateEvalResult`` rows with origin ``ask``, one per call
and question. Nothing here, and nothing an ask does, writes the call's own
verdict slot, its status or its run's.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class SimulateAsk(BaseModel):
    """One launch: the frozen selection, the questions as sent, what was shown."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"
        # Stopped before every grading ran: credits ran out, or usage was
        # denied. The gradings not run are cancelled with that cause.
        STOPPED = "stopped", "Stopped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    # FrozenSelection.as_json(): the ordered call ids, the person's own
    # description, who asked and when. Never worked out again.
    selection = models.JSONField()
    # Each question as sent: its key, name, criteria, model, answer shape,
    # inputs (key and kind), template, version and the readable parts of it.
    questions = models.JSONField()
    # The check before launch as the person saw it, and the credits shown.
    shown = models.JSONField()
    credits_estimate = models.FloatField(null=True, blank=True)
    asked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    stop_cause = models.CharField(max_length=64, blank=True, default="")
    # The engine that graded it and its own run id (the queued job's id).
    engine = models.CharField(max_length=64)
    engine_run_id = models.CharField(max_length=255, blank=True, default="")
    # Made once per check by the page and sent with its launch: a repeat of the
    # same launch (a retry after a lost response) returns this ask instead of
    # starting and charging a second one.
    launch_key = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "simulate_ask"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "launch_key"],
                condition=models.Q(launch_key__isnull=False),
                name="uniq_simulate_ask_org_launch_key",
            ),
        ]

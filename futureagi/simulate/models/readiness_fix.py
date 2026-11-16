"""A run readiness Fix: the builder chat fixing the rows a POST names, then autosave
and a re-check. ``commands`` and ``lines`` live on the run, serialized by its row lock."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models.harness_versioning import (
    HarnessEnvironmentVersion,
    HarnessSuiteVersion,
)
from tfc.utils.base_model import BaseModel

# The FE's FIX_ACTIVE_STATES: it polls while the run is in one of these.
ACTIVE = ("queued", "fixing", "waiting_on_you", "saving", "checking")
# A POST may add rows to a run in one of these.
APPENDABLE = ("queued", "fixing", "waiting_on_you")
# A line still being worked on.
OPEN_LINE = ("queued", "fixing", "waiting_on_you", "waiting_on_other")
LINE_STATES = (*OPEN_LINE, "fixed", "could_not_fix")


class ReadinessFixRun(BaseModel):
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        FIXING = "fixing", "Fixing"
        WAITING_ON_YOU = "waiting_on_you", "Waiting on you"
        SAVING = "saving", "Saving"
        CHECKING = "checking", "Checking"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        STOPPED = "stopped", "Stopped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The environment head.
    job = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    group = models.CharField(max_length=16, default="builder")
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    base_environment_version = models.ForeignKey(
        HarnessEnvironmentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    base_suite_version = models.ForeignKey(
        HarnessSuiteVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    result_environment_version = models.ForeignKey(
        HarnessEnvironmentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    result_suite_version = models.ForeignKey(
        HarnessSuiteVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # One entry per POST: {n, key, rows, message, message_id, state, *_at, outcome}.
    commands = models.JSONField(default=list, blank=True)
    # One entry per row: {row_id, area, label, state, reason, waiting_on, command}.
    lines = models.JSONField(default=list, blank=True)
    # The message in flight: the command, or the user's reply to its question.
    current_command_id = models.UUIDField(null=True, blank=True)
    # The open question of the current command.
    question_message_id = models.UUIDField(null=True, blank=True)
    # {"code", "message"}; also why a run ended without saving.
    error = models.JSONField(null=True, blank=True)
    # Autosave progress: {"phase", "name", "candidates", "validate_tries", "since",
    # "failures"}. Not ``save``: that would hide ``Model.save``.
    autosave = models.JSONField(null=True, blank=True)
    # Serialized as ``updated_at``: moves only when something visible changes.
    state_changed_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "simulate_readiness_fix_run"
        constraints = [
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(state__in=list(ACTIVE)),
                name="uniq_readiness_fix_active",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "-created_at"], name="idx_rfix_job_created"),
        ]

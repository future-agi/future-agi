import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentFixRun(BaseModel):
    """One "Fix my agent" run: the improvement engine works on an environment's agent in a
    sandbox and proposes candidate agent versions, proven on the environment's scenarios."""

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        STOPPING = "stopping", "Stopping"
        READY = "ready", "Ready for review"
        NO_IMPROVEMENT = "no_improvement", "No safe improvement"
        NOT_REPRODUCED = "not_reproduced", "Could not reproduce"
        OUT_OF_BUDGET = "out_of_budget", "Stopped at budget"
        STOPPED = "stopped", "Stopped"
        FAILED = "failed", "Failed"

    ACTIVE_STATES = (State.QUEUED, State.RUNNING, State.STOPPING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    environment = models.ForeignKey(
        "simulate.HostedHarnessJob", on_delete=models.CASCADE, related_name="+"
    )
    source_run = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    base_agent_version = models.ForeignKey(
        "simulate.AgentVersion", on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    state = models.CharField(max_length=32, choices=State.choices, default=State.QUEUED)
    # The engine's current stage, as it last reported it.
    stage = models.CharField(max_length=32, blank=True, default="")
    # What the user asked for (simulate.pydantic_schemas.agent_fix.FixRequest).
    request = models.JSONField()
    spent_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    # The engine's closing report (simulate.pydantic_schemas.agent_fix.FixReport).
    report = models.JSONField(null=True, blank=True)
    failure = models.JSONField(null=True, blank=True)
    checkpoint_key = models.CharField(max_length=512, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # The sandbox now running the engine. A relaunch rotates the token and the fence,
    # so a sandbox that outlived its attempt can no longer call back.
    sandbox_attempt = models.PositiveIntegerField(default=0)
    sandbox_ref = models.CharField(max_length=255, blank=True, default="")
    token_hash = models.CharField(max_length=64, blank=True, default="")
    fence_hash = models.CharField(max_length=64, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "simulate_agent_fix_run"
        constraints = [
            models.UniqueConstraint(
                fields=["environment"],
                condition=models.Q(state__in=["queued", "running", "stopping"]),
                name="uniq_active_agent_fix_per_environment",
            ),
        ]
        indexes = [
            models.Index(
                fields=["environment", "-created_at"], name="idx_agent_fix_env_created"
            ),
        ]


class AgentFixCandidate(BaseModel):
    """One change the engine proposed, stored as an agent version that is never made the
    environment's default unless the user asks."""

    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        REJECTED = "rejected", "Rejected"
        FINALIST = "finalist", "Finalist"
        WINNER = "winner", "Winner"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fix_run = models.ForeignKey(
        AgentFixRun, on_delete=models.CASCADE, related_name="candidates"
    )
    attempt = models.PositiveSmallIntegerField()
    agent_version = models.ForeignKey(
        "simulate.AgentVersion", on_delete=models.PROTECT, related_name="+"
    )
    # The candidate's change against the base version, as a git patch.
    patch_object_key = models.CharField(max_length=512)
    summary = models.TextField(blank=True, default="")
    outcome = models.CharField(
        max_length=16, choices=Outcome.choices, default=Outcome.PENDING
    )
    verdict = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "simulate_agent_fix_candidate"
        ordering = ("attempt",)
        constraints = [
            models.UniqueConstraint(
                fields=["fix_run", "attempt"], name="uniq_agent_fix_candidate_attempt"
            ),
        ]

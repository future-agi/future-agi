import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class ReplayType(models.TextChoices):
    SESSION = "session", "Session"
    TRACE = "trace", "Trace"


class ReplaySessionStep(models.TextChoices):
    INIT = "init", "Init"
    GENERATING = "generating", "Generating Scenario"
    COMPLETED = "completed", "Completed"


class ReplaySession(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="replay_sessions",
    )
    replay_type = models.CharField(max_length=255, choices=ReplayType.choices)
    ids = models.JSONField(null=True, blank=True)
    select_all = models.BooleanField(default=False)

    current_step = models.CharField(
        max_length=20,
        choices=ReplaySessionStep.choices,
        default=ReplaySessionStep.INIT,
    )

    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replay_sessions",
    )
    scenario = models.ForeignKey(
        "simulate.Scenarios",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replay_sessions",
    )
    run_test = models.ForeignKey(
        "simulate.RunTest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replay_sessions",
    )

    def __str__(self):
        return f"ReplaySession {self.id} - {self.project.name} ({self.current_step})"

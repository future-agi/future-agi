import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class ChatSimulatorAssistant(BaseModel):
    """Persistent config for a chat simulator assistant (simulator persona)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=512)
    system_prompt = models.TextField()
    model = models.CharField(max_length=512)
    temperature = models.FloatField(default=0.9)
    max_tokens = models.IntegerField(default=800)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="chat_simulator_assistants",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_simulator_assistants",
    )

    class Meta:
        app_label = "simulate"


class ChatSimulatorSession(BaseModel):
    """Persistent state for a chat simulation session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assistant = models.ForeignKey(
        ChatSimulatorAssistant,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    call_execution = models.OneToOneField(
        "simulate.CallExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_simulator_session",
    )
    messages = models.JSONField(default=list)
    status = models.CharField(max_length=50, default="active")
    has_chat_ended = models.BooleanField(default=False)
    total_tokens = models.IntegerField(default=0)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="chat_simulator_sessions",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_simulator_sessions",
    )

    class Meta:
        app_label = "simulate"

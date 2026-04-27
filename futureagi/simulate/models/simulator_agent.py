import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class SimulatorAgent(BaseModel):
    """Model for simulator agents with voice and conversation settings"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="Name of the simulator agent")
    prompt = models.TextField(help_text="System prompt for the agent")
    voice_provider = models.CharField(
        max_length=100, help_text="Voice service provider"
    )
    voice_name = models.CharField(max_length=100, help_text="Specific voice to use")
    interrupt_sensitivity = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(11.0)],
        default=0.5,
        help_text="Sensitivity for interruption detection (0-1)",
    )
    conversation_speed = models.FloatField(
        validators=[MinValueValidator(0.1), MaxValueValidator(2.0)],
        default=1.0,
        help_text="Speed of conversation (0.1-3.0)",
    )
    finished_speaking_sensitivity = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(11.0)],
        default=0.5,
        help_text="Sensitivity for detecting when speaker has finished (0-1)",
    )
    model = models.CharField(max_length=100, help_text="LLM model to use")
    llm_temperature = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(2.0)],
        default=0.7,
        help_text="Temperature setting for LLM (0-2)",
    )
    max_call_duration_in_minutes = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(180)],
        default=30,
        help_text="Maximum call duration in minutes (1-180)",
    )
    initial_message_delay = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(60)],
        default=0,
        help_text="Delay before initial message in seconds (0-60)",
    )
    initial_message = models.TextField(
        blank=True,
        default="",
        help_text="Initial message to send when conversation starts",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="simulator_agents",
        help_text="Organization this simulator agent belongs to",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="simulator_agents",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "simulator_agents"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

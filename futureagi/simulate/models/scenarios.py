import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from tfc.utils.base_model import BaseModel


class Scenarios(BaseModel):
    """
    Model to store different types of simulation scenarios
    """

    class ScenarioTypes(models.TextChoices):
        GRAPH = "graph", "Graph"
        SCRIPT = "script", "Script"
        DATASET = "dataset", "Dataset"

    class SourceTypes(models.TextChoices):
        AGENT_DEFINITION = "agent_definition", "Agent Definition"
        PROMPT = "prompt", "Prompt"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255, help_text="Name of the scenario")

    description = models.TextField(
        blank=True, null=True, help_text="Optional description of the scenario"
    )

    source = models.TextField(help_text="Source content or reference for the scenario")

    scenario_type = models.CharField(
        max_length=20,
        choices=ScenarioTypes.choices,
        default=ScenarioTypes.DATASET,
        help_text="Type of scenario (graph, script, or dataset)",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="scenarios",
        help_text="Organization this scenario belongs to",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="scenarios",
        null=True,
        blank=True,
    )

    dataset = models.ForeignKey(
        "model_hub.Dataset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scenarios",
        help_text="Dataset associated with this scenario (only for dataset type scenarios)",
    )

    agent_definition = models.ForeignKey(
        "simulate.AgentDefinition",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scenarios",
        help_text="Agent definition associated with this scenario",
    )

    source_type = models.CharField(
        max_length=20,
        choices=SourceTypes.choices,
        default=SourceTypes.AGENT_DEFINITION,
        help_text="Source type for the scenario: agent_definition or prompt",
    )

    prompt_template = models.ForeignKey(
        "model_hub.PromptTemplate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scenarios",
        help_text="Prompt template associated with this scenario (only for prompt source type)",
    )

    prompt_version = models.ForeignKey(
        "model_hub.PromptVersion",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scenarios",
        help_text="Prompt version associated with this scenario (only for prompt source type)",
    )

    simulator_agent = models.ForeignKey(
        "simulate.SimulatorAgent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scenarios",
        help_text="Simulator agent associated with this scenario",
    )

    metadata = models.JSONField(
        default=dict, blank=True, help_text="Metadata associated with this scenario"
    )
    status = models.CharField(
        max_length=50,
        choices=StatusType.get_choices(),
        default=StatusType.RUNNING.value,
        help_text="Status of the scenario",
    )

    class Meta:
        db_table = "simulate_scenarios"
        verbose_name = "Scenario"
        verbose_name_plural = "Scenarios"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name}"

    def clean(self):
        """Custom validation"""
        if not self.name.strip():
            raise ValidationError({"name": "Name cannot be empty or just whitespace."})

        if not self.source.strip():
            raise ValidationError(
                {"source": "Source cannot be empty or just whitespace."}
            )

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models.agent_version import AgentVersion
from tfc.utils.base_model import BaseModel

from .agent_definition import AgentDefinition
from .scenarios import Scenarios
from .simulator_agent import SimulatorAgent


class RunTest(BaseModel):
    """
    Model to store test runs with agent definitions, scenarios, and evaluations
    """

    class SourceTypes(models.TextChoices):
        AGENT_DEFINITION = "agent_definition", "Agent Definition"
        PROMPT = "prompt", "Prompt"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255, help_text="Name of the test run")

    description = models.TextField(
        blank=True, null=True, help_text="Description of the test run"
    )

    agent_definition = models.ForeignKey(
        AgentDefinition,
        on_delete=models.CASCADE,
        related_name="run_tests",
        help_text="Agent definition for this test run",
        null=True,
        blank=True,
    )

    agent_version = models.ForeignKey(
        AgentVersion,
        on_delete=models.CASCADE,
        related_name="run_tests",
        help_text="Agent version for this test run",
        null=True,
        blank=True,
    )

    source_type = models.CharField(
        max_length=20,
        choices=SourceTypes.choices,
        default=SourceTypes.AGENT_DEFINITION,
        help_text="Source type for the test run: agent_definition or prompt",
    )

    prompt_template = models.ForeignKey(
        "model_hub.PromptTemplate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="run_tests",
        help_text="Prompt template for this test run (only for prompt source type)",
    )

    prompt_version = models.ForeignKey(
        "model_hub.PromptVersion",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="run_tests",
        help_text="Prompt version for this test run (only for prompt source type)",
    )

    scenarios = models.ManyToManyField(
        Scenarios, related_name="run_tests", help_text="Scenarios to run in this test"
    )

    dataset_row_ids: ArrayField = ArrayField(
        models.CharField(max_length=255),
        blank=True,
        default=list,
        help_text="IDs of dataset rows to run evaluations on",
    )

    simulator_agent = models.ForeignKey(
        SimulatorAgent,
        on_delete=models.CASCADE,
        related_name="run_tests",
        null=True,
        blank=True,
        help_text="Simulator agent for this test run (derived from scenarios)",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="run_tests",
        help_text="Organization this test run belongs to",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="run_tests",
        null=True,
        blank=True,
    )

    enable_tool_evaluation = models.BooleanField(
        default=False, help_text="Enable automatic tool evaluation for this test run"
    )

    class Meta:
        db_table = "simulate_run_test"
        verbose_name = "Run Test"
        verbose_name_plural = "Run Tests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization_id"], name="idx_runtest_organization_id"
            ),
        ]

    def __str__(self):
        if self.agent_definition:
            return f"{self.name} - {self.agent_definition.agent_name}"
        return self.name


class CreateCallExecution(BaseModel):
    """
    Model to store call count
    """

    class CallStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        REGISTERED = "registered", "Registered"
        ONGOING = "ongoing", "Ongoing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    phone_number_id = models.CharField(
        max_length=255, help_text="The phone number ID to create the call execution for"
    )

    to_number = models.CharField(
        max_length=255, help_text="The phone number to create the call execution for"
    )

    system_prompt = models.TextField(
        help_text="The system prompt to create the call execution for"
    )

    metadata = models.JSONField(
        help_text="The metadata to create the call execution for"
    )

    voice_settings = models.JSONField(
        help_text="The voice settings to create the call execution for"
    )

    call_execution = models.ForeignKey(
        "simulate.CallExecution",
        on_delete=models.CASCADE,
        related_name="create_call_executions",
        help_text="The call execution this call count belongs to",
    )

    status = models.CharField(
        max_length=255,
        choices=CallStatus.choices,
        default=CallStatus.REGISTERED,
        help_text="The status of the call execution ",
    )

    class Meta:
        db_table = "simulate_createcallexecution"
        verbose_name = "Create Call Execution"
        verbose_name_plural = "Create Call Executions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "deleted", "created_at"], name="idx_createcall_status"
            ),
        ]

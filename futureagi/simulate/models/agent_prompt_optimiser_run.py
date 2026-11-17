import uuid

from django.db import models, router

from simulate.models.agent_optimiser import AgentOptimiser
from simulate.models.agent_optimiser_run import AgentOptimiserRun
from simulate.models.test_execution import TestExecution
from tfc.utils.base_model import BaseModel


class AgentPromptOptimiserRun(BaseModel):
    """
    Represents a single run of an agent prompt optimiser.
    It takes inputs from a test execution and an agent optimiser run.
    """

    class OptimiserType(models.TextChoices):
        RANDOM_SEARCH = "random_search", "Random Search"
        GEPA = "gepa", "GEPA"
        PROTEGI = "protegi", "Protegi"
        BAYESIAN = "bayesian", "Bayesian"
        METAPROMPT = "metaprompt", "Metaprompt"
        PROMPTWIZARD = "promptwizard", "PromptWizard"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    agent_optimiser = models.ForeignKey(
        AgentOptimiser, on_delete=models.CASCADE, related_name="prompt_optimiser_runs"
    )
    agent_optimiser_run = models.ForeignKey(
        AgentOptimiserRun,
        on_delete=models.CASCADE,
        related_name="prompt_optimiser_runs",
    )
    test_execution = models.ForeignKey(
        TestExecution, on_delete=models.CASCADE, related_name="prompt_optimiser_runs"
    )
    optimiser_type = models.CharField(max_length=50, choices=OptimiserType.choices)
    model = models.CharField(
        max_length=255, help_text="LLM model used for the optimiser run"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    result = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    configuration = models.JSONField(null=True)
    tenant_lineage = models.JSONField(null=True, editable=False)

    class Meta:
        db_table = "agent_prompt_optimiser_run"
        indexes = [
            models.Index(fields=["status"], name="idx_prompt_opt_run_status"),
            models.Index(
                fields=["test_execution", "status"],
                name="idx_prompt_opt_run_test_status",
            ),
        ]

    def save(self, *args, **kwargs):
        from simulate.utils.prompt_optimizer_scope import (
            PromptOptimiserScopeError,
            creation_lineage,
        )

        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            # Only new objects may establish provenance. Ignore supplied snapshots.
            self.tenant_lineage = creation_lineage(self, using)
        else:
            original = (
                type(self)
                .all_objects.using(using)
                .filter(pk=self.pk)
                .values_list("tenant_lineage", flat=True)
                .get()
            )
            if self.tenant_lineage != original:
                raise PromptOptimiserScopeError(
                    "Prompt optimizer creation scope is immutable."
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.agent_optimiser.name} - {self.id} - {self.status}"

    def mark_as_running(self):
        """Mark the prompt optimiser run as running"""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status", "updated_at"])

    def mark_as_completed(self):
        """Mark the prompt optimiser run as completed"""
        self.status = self.Status.COMPLETED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_failed(self, error_message: str = None):
        """Mark the prompt optimiser run as failed"""
        self.status = self.Status.FAILED
        self.error_message = error_message
        self.save(update_fields=["status", "error_message", "updated_at"])

    def get_next_step_number(self) -> int:
        """
        Calculates the next available step number for this run.
        """
        last_step = self.steps.order_by("-step_number").first()
        if last_step:
            return last_step.step_number + 1
        return 1

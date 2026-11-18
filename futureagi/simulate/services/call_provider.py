"""Attribute a simulation call to its target agent, not its recording transport."""

from django.db.models import Case, F, TextField, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import NullIf

from simulate.models import AgentVersion, CallExecution, TestExecution


def execution_agent_provider(
    execution: TestExecution, call_version: AgentVersion | None = None
) -> str | None:
    version = call_version or getattr(execution, "agent_version", None)
    if version is not None:
        return version.configuration_snapshot.get("provider") or None
    return getattr(execution.agent_definition, "provider", None) or None


def call_agent_provider(call: CallExecution) -> str | None:
    if hasattr(call, "result_provider"):
        return call.result_provider
    return execution_agent_provider(
        call.test_execution, getattr(call, "agent_version", None)
    )


def call_agent_provider_expression() -> NullIf:
    """SQL equivalent of execution_agent_provider for pages, exports and aggregation."""
    return NullIf(
        Case(
            When(
                agent_version_id__isnull=False,
                then=KeyTextTransform(
                    "provider", "agent_version__configuration_snapshot"
                ),
            ),
            When(
                test_execution__agent_version_id__isnull=False,
                then=KeyTextTransform(
                    "provider", "test_execution__agent_version__configuration_snapshot"
                ),
            ),
            default=F("test_execution__agent_definition__provider"),
            output_field=TextField(),
        ),
        Value("", output_field=TextField()),
    )

"""Attribute a simulation call to its target agent, not its recording transport."""

from django.db.models import Case, F, Func, JSONField, TextField, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf

from simulate.models import AgentVersion, CallExecution, TestExecution

HOSTED_TARGET_PROVIDER = "hosted_target_provider"


def record_hosted_voice_target(calls, version, provider: str) -> None:
    """Stamp only this dispatch's precreated rows. /batch adopts them in place."""
    calls.update(
        agent_version=version,
        call_metadata=Func(
            F("call_metadata"),
            Value([HOSTED_TARGET_PROVIDER]),
            Value(provider, output_field=JSONField()),
            function="jsonb_set",
            output_field=JSONField(),
        ),
    )


def _configured_provider(
    execution: TestExecution, call_version: AgentVersion | None = None
) -> str | None:
    version = call_version or getattr(execution, "agent_version", None)
    if version is not None:
        return version.configuration_snapshot.get("provider") or None
    return getattr(execution.agent_definition, "provider", None) or None


def execution_agent_provider(execution: TestExecution) -> str | None:
    # A partial rerun may have called a different target. Do not present a
    # single provider for the entire run if the recorded calls disagree.
    calls = execution.calls.filter(deleted=False)
    if calls.filter(call_metadata__has_key=HOSTED_TARGET_PROVIDER).exists():
        providers = list(
            calls.order_by()
            .annotate(result_provider=call_agent_provider_expression())
            .values_list("result_provider", flat=True)
            .distinct()[:2]
        )
        return providers[0] if len(providers) == 1 else None
    return _configured_provider(execution)


def call_agent_provider(call: CallExecution) -> str | None:
    if hasattr(call, "result_provider"):
        return call.result_provider
    metadata = call.call_metadata
    if isinstance(metadata, dict) and metadata.get(HOSTED_TARGET_PROVIDER):
        return metadata[HOSTED_TARGET_PROVIDER]
    return _configured_provider(
        call.test_execution, getattr(call, "agent_version", None)
    )


def call_agent_provider_expression() -> Coalesce:
    """SQL equivalent of call_agent_provider for pages, exports and aggregation."""
    return Coalesce(
        KeyTextTransform(HOSTED_TARGET_PROVIDER, "call_metadata"),
        NullIf(
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
                        "provider",
                        "test_execution__agent_version__configuration_snapshot",
                    ),
                ),
                default=F("test_execution__agent_definition__provider"),
                output_field=TextField(),
            ),
            Value("", output_field=TextField()),
        ),
    )

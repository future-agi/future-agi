"""Attribute a simulation call to its target agent, not its recording transport."""

from typing import cast

from django.db.models import Case, F, Func, JSONField, TextField, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf

from simulate.models import AgentVersion, CallExecution, TestExecution
from tracer.models.observability_provider import ProviderChoices

HOSTED_TARGET_PROVIDER = "hosted_target_provider"
# Legacy call-detail order, used only when no target provider was recorded or configured.
_PAYLOAD_PROVIDER_ORDER = (
    ProviderChoices.VAPI.value,
    ProviderChoices.RETELL.value,
    ProviderChoices.LIVEKIT.value,
    ProviderChoices.ELEVEN_LABS.value,
    ProviderChoices.OTHERS.value,
)


def public_provider_call_data(data):
    """Project LiveKit retention state without changing the stored provider payload."""
    key = ProviderChoices.LIVEKIT.value
    livekit = data.get(key) if isinstance(data, dict) else None
    if not isinstance(livekit, dict):
        return data
    private = {"recording_cleanup_intent", "recording_history", "recording_scope"}
    return {**data, key: {k: v for k, v in livekit.items() if k not in private}}


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
    for version in (call_version, getattr(execution, "agent_version", None)):
        if version is not None and isinstance(version.configuration_snapshot, dict):
            if provider := version.configuration_snapshot.get("provider"):
                return cast("str", provider)
    return getattr(execution.agent_definition, "provider", None) or None


def _payload_provider(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    for provider in _PAYLOAD_PROVIDER_ORDER:
        payload = data.get(provider)
        if isinstance(payload, dict) and payload:
            return provider
    return min(
        (key for key, value in data.items() if isinstance(value, dict) and value),
        default=None,
    )


def execution_agent_provider(execution: TestExecution) -> str | None:
    # A partial rerun may have called a different target. Do not present a
    # single provider for the entire run if the recorded calls disagree.
    providers = list(
        execution.calls.filter(deleted=False)
        .order_by()
        .annotate(result_provider=call_agent_provider_expression())
        .values_list("result_provider", flat=True)
        .distinct()[:2]
    )
    if not providers:
        return _configured_provider(execution)
    return providers[0] if len(providers) == 1 else None


def call_agent_provider(call: CallExecution) -> str | None:
    if hasattr(call, "result_provider"):
        return cast("str | None", call.result_provider)
    metadata = call.call_metadata
    if isinstance(metadata, dict) and metadata.get(HOSTED_TARGET_PROVIDER):
        return cast("str", metadata[HOSTED_TARGET_PROVIDER])
    return _configured_provider(
        call.test_execution, getattr(call, "agent_version", None)
    ) or _payload_provider(call.provider_call_data)


class _FirstObjectPayloadKey(Func):
    """Lexically first custom key whose stored payload is a non-empty object."""

    output_field = TextField()
    # JSONB's storage order and the database's default collation may differ
    # from Python's lexical order. C collation compares the raw UTF-8 bytes.
    template = (
        "(CASE WHEN jsonb_typeof(%(expressions)s) = 'object' THEN "
        "(SELECT p.key FROM jsonb_each(%(expressions)s) AS p "
        "WHERE jsonb_typeof(p.value) = 'object' AND p.value <> '{}'::jsonb "
        'ORDER BY p.key COLLATE "C" LIMIT 1) END)'
    )


def call_agent_provider_expression() -> Coalesce:
    """SQL equivalent of call_agent_provider for pages, exports and aggregation."""
    empty = Value("", output_field=TextField())
    return Coalesce(
        NullIf(KeyTextTransform(HOSTED_TARGET_PROVIDER, "call_metadata"), empty),
        NullIf(
            KeyTextTransform("provider", "agent_version__configuration_snapshot"), empty
        ),
        NullIf(
            KeyTextTransform(
                "provider", "test_execution__agent_version__configuration_snapshot"
            ),
            empty,
        ),
        NullIf(F("test_execution__agent_definition__provider"), empty),
        Case(
            *(
                # PostgreSQL sorts all non-objects below objects, and an empty
                # object below every non-empty object: > {} matches exactly the
                # same payloads as `isinstance(payload, dict) and payload`.
                When(
                    provider_call_data__has_key=provider,
                    **{f"provider_call_data__{provider}__gt": {}},
                    then=Value(provider),
                )
                for provider in _PAYLOAD_PROVIDER_ORDER
            ),
            default=_FirstObjectPayloadKey("provider_call_data"),
            output_field=TextField(),
        ),
        output_field=TextField(),
    )

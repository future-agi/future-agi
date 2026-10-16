"""Agent identity is independent of the transport carrying a simulation call."""

from django.db.models import Case, CharField, F, Func, JSONField, Q, Value, When
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Coalesce, NullIf
from django.db.models.lookups import Exact

from simulate.semantics import SupportedProviders

# "livekit" is excluded: hosted ALK stores it for any call with a tool trace,
# whatever the target, so it would label some calls of one agent and not others.
PAYLOAD_PROVIDERS = tuple(sorted(set(SupportedProviders) - {"livekit"}))


def call_provider(call):
    execution = call.test_execution
    # Hosted and SDK runs pin the version on each call rather than on the run.
    version = execution.agent_version or call.agent_version
    snapshot = getattr(version, "configuration_snapshot", None)
    if isinstance(snapshot, dict):
        provider = snapshot.get("provider")
    else:
        provider = getattr(execution.agent_definition, "provider", None)
    if provider:
        return provider
    payload = call.provider_call_data
    if isinstance(payload, dict):
        for name in PAYLOAD_PROVIDERS:
            if isinstance(payload.get(name), dict) and payload[name]:
                return name
    return None


def call_provider_expression():
    payload_cases = []
    for name in PAYLOAD_PROVIDERS:
        payload = KeyTransform(name, F("provider_call_data"))
        payload_cases.append(
            When(
                Q(
                    Exact(
                        Func(
                            payload, function="jsonb_typeof", output_field=CharField()
                        ),
                        Value("object"),
                    )
                )
                & ~Q(Exact(payload, Value({}, output_field=JSONField()))),
                then=Value(name),
            )
        )
    return Coalesce(
        Case(
            When(
                test_execution__agent_version__isnull=False,
                then=NullIf(
                    KeyTextTransform(
                        "provider",
                        F("test_execution__agent_version__configuration_snapshot"),
                    ),
                    Value(""),
                ),
            ),
            When(
                agent_version__isnull=False,
                then=NullIf(
                    KeyTextTransform(
                        "provider", F("agent_version__configuration_snapshot")
                    ),
                    Value(""),
                ),
            ),
            default=NullIf(F("test_execution__agent_definition__provider"), Value("")),
            output_field=CharField(),
        ),
        Case(*payload_cases, output_field=CharField()),
        Value("Unknown"),
        output_field=CharField(),
    )

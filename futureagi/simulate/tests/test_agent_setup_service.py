import copy
import sys
import types
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from simulate.models import AgentDefinition, AgentVersion, ProviderCredentials
from simulate.models.agent_setup import (
    AgentBehaviourUnit,
    AgentTelephony,
    AgentTool,
    AgentVersionSetup,
)
from simulate.services import agent_setup as service
from simulate.services.agent_setup import (
    AgentNotFound,
    AgentStoreError,
    agent_summary,
    setup_outcome,
    store_agent,
    store_setup_failure,
    stored_agent,
)

HASH_A = "sh1:" + "a" * 64
HASH_B = "sh1:" + "b" * 64


def source_agent(**overrides):
    agent = {
        "schema_version": "1.0",
        "type": "chat",
        "kind": "source",
        "name": "harbor_hotels",
        "provider_ref": None,
        "behaviour": [
            {
                "id": "main",
                "shape": "external",
                "instructions": "You are the booking assistant for Harbor Hotels.",
                "tools": ["get_booking"],
                "transitions": [],
            }
        ],
        "tools": [
            {
                "name": "get_booking",
                "description": "Look up a booking by its id.",
                "kind": "builtin",
                "parameters": {
                    "type": "object",
                    "properties": {"booking_id": {}},
                    "required": ["booking_id"],
                },
                "endpoint": None,
            }
        ],
        "model": None,
        "voice": None,
        "telephony": [],
        "supports": [],
        "direction_answer": None,
        "gaps": ["flow_not_described"],
        "instrumentation": "traceai",
        "extension": {"contract": {"agent": "harbor_hotels", "api_key": "[redacted]"}},
        "setup_hash": HASH_A,
    }
    agent.update(overrides)
    return agent


def hosted_agent(**overrides):
    agent = {
        "schema_version": "1.0",
        "type": "voice",
        "kind": "hosted",
        "name": "Support line",
        "provider_ref": {"provider": "vapi", "id": "asst_1", "version": "3"},
        "behaviour": [
            {
                "id": "main",
                "shape": "single_prompt",
                "instructions": "You are a support agent.",
                "tools": ["lookup_order", "search_kb"],
                "transitions": ["billing"],
            },
            {
                "id": "billing",
                "shape": "flow_node",
                "instructions": None,
                "tools": [],
                "transitions": [],
            },
        ],
        "tools": [
            {
                "name": "lookup_order",
                "description": "Find an order",
                "kind": "http",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                },
                "endpoint": {"url": "https://tools.example.com/lookup"},
            },
            {
                "name": "search_kb",
                "description": None,
                "kind": "mcp",
                "parameters": None,
                "endpoint": None,
            },
        ],
        "model": {
            "provider": "openai",
            "name": "gpt-4o",
            "temperature": 0.3,
            "max_tokens": 250,
        },
        "voice": {
            "provider": "11labs",
            "voice_id": "marissa",
            "language": "en",
            "speed": 1.0,
        },
        "telephony": [
            {
                "number": "+15550100",
                "provider": "twilio",
                "direction_supported": ["inbound", "outbound"],
            },
            {"number": "+15550101", "provider": None, "direction_supported": []},
        ],
        "supports": ["inbound", "outbound"],
        "direction_answer": None,
        "gaps": [],
        "instrumentation": "none",
        "extension": {"assistant": {"name": "Support line"}},
        "setup_hash": HASH_A,
    }
    agent.update(overrides)
    return agent


class ProviderListNotPublished(Exception):
    pass


class ProviderListVersionNotFound(Exception):
    pass


@pytest.fixture
def provider_list(monkeypatch):
    module = types.ModuleType("simulate.services.provider_list")
    module.ProviderListNotPublished = ProviderListNotPublished
    module.ProviderListVersionNotFound = ProviderListVersionNotFound
    module.listed = {"vapi"}
    module.published = True
    module.calls = 0

    def read_provider_list(*, sdk_version=None):
        module.calls += 1
        if not module.published:
            raise ProviderListNotPublished("not published")
        return types.SimpleNamespace(providers={name: object() for name in module.listed})

    module.read_provider_list = read_provider_list
    monkeypatch.setitem(sys.modules, "simulate.services.provider_list", module)
    return module


@pytest.fixture
def definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Harbor Hotels",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="SDK-provisioned text agent",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def version(definition):
    return definition.create_version(
        description="", commit_message="first", status="active"
    )


@pytest.fixture
def hosted_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Support line",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Support",
        provider="vapi",
        assistant_id="asst_1",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def hosted_version(hosted_definition):
    return hosted_definition.create_version(
        description="", commit_message="connect", status="active"
    )


def _rows():
    return (
        AgentVersionSetup.all_objects.count(),
        AgentBehaviourUnit.all_objects.count(),
        AgentTool.all_objects.count(),
        AgentTelephony.all_objects.count(),
    )


def _without_extension(agent):
    return {key: value for key, value in agent.items() if key != "extension"}


@pytest.mark.django_db
def test_source_agent_round_trips(version):
    agent = source_agent()

    assert store_agent(agent_version_id=version.id, agent=agent, sdk_version="0.2.3") == "stored"

    assert stored_agent(version.id, with_raw_setup=True) == agent
    assert stored_agent(version.id) == _without_extension(agent)


@pytest.mark.django_db
def test_hosted_agent_with_every_column_round_trips(hosted_version, provider_list):
    agent = hosted_agent()

    assert (
        store_agent(agent_version_id=hosted_version.id, agent=agent, sdk_version="0.2.3")
        == "stored"
    )

    assert stored_agent(hosted_version.id, with_raw_setup=True) == agent
    assert _rows() == (1, 2, 2, 2)


@pytest.mark.django_db
def test_an_older_guest_without_instrumentation_is_stored_as_none(version):
    agent = source_agent()
    agent.pop("instrumentation")

    store_agent(agent_version_id=version.id, agent=agent, sdk_version="0.2.3")

    assert stored_agent(version.id)["instrumentation"] == "none"


def _invalid(mutate):
    agent = source_agent()
    mutate(agent)
    return agent


SHAPE_REFUSALS = {
    "unknown_top_level_key": lambda a: a.update(owner="me"),
    "unknown_nested_key": lambda a: a["behaviour"][0].update(owner="me"),
    "missing_field": lambda a: a.pop("gaps"),
    "source_with_provider_ref": lambda a: a.update(provider_ref={"provider": "vapi", "id": "x"}),
    "hosted_without_provider_ref": lambda a: a.update(kind="hosted"),
    "empty_behaviour": lambda a: a.update(behaviour=[]),
    "repeated_unit_id": lambda a: a["behaviour"].append(copy.deepcopy(a["behaviour"][0])),
    "unit_id_pattern": lambda a: a["behaviour"][0].update(id="Main Unit"),
    "unit_tool_unknown": lambda a: a["behaviour"][0]["tools"].append("nope"),
    "unknown_type": lambda a: a.update(type="email"),
    "unknown_kind": lambda a: a.update(kind="robot"),
    "unknown_shape": lambda a: a["behaviour"][0].update(shape="loop"),
    "unknown_tool_kind": lambda a: a["tools"][0].update(kind="grpc"),
    "unknown_gap": lambda a: a.update(gaps=["unknown"]),
    "unknown_instrumentation": lambda a: a.update(instrumentation="langfuse"),
    "supports_unsorted": lambda a: a.update(supports=["outbound", "inbound"]),
    "supports_repeated": lambda a: a.update(supports=["inbound", "inbound"]),
    "schema_version": lambda a: a.update(schema_version="2.0"),
    "setup_hash_format": lambda a: a.update(setup_hash="sh1:xyz"),
    "setup_hash_trailing_newline": lambda a: a.update(setup_hash=HASH_A + "\n"),
    "name_too_long": lambda a: a.update(name="x" * 256),
    "parameters_not_object": lambda a: a["tools"][0].update(parameters=[1]),
    "boolean_as_number": lambda a: a.update(model={"name": "m", "temperature": True}),
    "extension_not_object": lambda a: a.update(extension=[]),
}


@pytest.mark.django_db
@pytest.mark.parametrize("mutate", SHAPE_REFUSALS.values(), ids=SHAPE_REFUSALS.keys())
def test_store_repeats_the_sdk_shape_checks(version, provider_list, mutate):
    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=version.id, agent=_invalid(mutate), sdk_version="0.2.3")

    assert raised.value.code == "agent_invalid"
    assert _rows() == (0, 0, 0, 0)
    assert provider_list.calls == 0


@pytest.mark.django_db
@pytest.mark.parametrize("sdk_version", ["", "x" * 33, 3])
def test_store_refuses_a_bad_sdk_version(version, sdk_version):
    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version=sdk_version)

    assert raised.value.code == "agent_invalid"


@pytest.mark.django_db
def test_store_accepts_null_parameters_null_model_and_extension_keys_of_its_own(version):
    agent = source_agent()
    agent["tools"][0]["parameters"] = None
    agent["extension"] = {"contract": {"anything": {"nested": [1, 2]}}, "extra": True}

    assert store_agent(agent_version_id=version.id, agent=agent, sdk_version="0.2.3") == "stored"


@pytest.mark.django_db
def test_store_rejects_in_order(version, monkeypatch):
    monkeypatch.setattr(service, "AGENT_MAX_BYTES", 10)
    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=uuid.uuid4(), agent=_invalid(lambda a: a.update(type="x")), sdk_version="0.2.3")
    assert raised.value.code == "agent_too_large"

    monkeypatch.setattr(service, "AGENT_MAX_BYTES", 1_048_576)
    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=uuid.uuid4(), agent=_invalid(lambda a: a.update(type="x")), sdk_version="0.2.3")
    assert raised.value.code == "agent_version_not_found"

    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=version.id, agent=float("nan"), sdk_version="0.2.3")
    assert raised.value.code == "agent_invalid"
    assert _rows() == (0, 0, 0, 0)


@pytest.mark.django_db
def test_store_refuses_a_deleted_version(version):
    version.delete()

    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    assert raised.value.code == "agent_version_not_found"


@pytest.mark.django_db
def test_source_agent_never_reads_the_provider_list(version, monkeypatch):
    monkeypatch.delitem(sys.modules, "simulate.services.provider_list", raising=False)

    assert store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3") == "stored"


@pytest.mark.django_db
def test_hosted_agent_needs_a_published_provider_list(hosted_version, provider_list):
    provider_list.published = False

    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=hosted_version.id, agent=hosted_agent(), sdk_version="0.2.3")

    assert raised.value.code == "agent_schema_unavailable"
    assert _rows() == (0, 0, 0, 0)


@pytest.mark.django_db
def test_hosted_agent_needs_a_listed_provider(hosted_version, provider_list):
    provider_list.listed = {"retell"}

    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=hosted_version.id, agent=hosted_agent(), sdk_version="0.2.3")

    assert raised.value.code == "provider_not_listed"


@pytest.mark.django_db
@pytest.mark.parametrize(("assistant_id", "outcome"), [("asst_other", "refused"), ("asst_1", "stored")])
def test_hosted_agent_must_match_the_versions_credential_row(
    hosted_version, provider_list, assistant_id, outcome
):
    ProviderCredentials.objects.create(
        agent_version=hosted_version,
        provider_type="vapi",
        api_key="sk-test",
        assistant_id=assistant_id,
    )

    if outcome == "stored":
        assert store_agent(agent_version_id=hosted_version.id, agent=hosted_agent(), sdk_version="0.2.3") == "stored"
        return
    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=hosted_version.id, agent=hosted_agent(), sdk_version="0.2.3")
    assert raised.value.code == "provider_ref_mismatch"


@pytest.mark.django_db
def test_first_outcome_wins(version):
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    assert store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3") == "unchanged"
    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=version.id, agent=source_agent(setup_hash=HASH_B), sdk_version="0.2.3")
    assert raised.value.code == "setup_outcome_already_stored"
    assert store_setup_failure(agent_version_id=version.id, code="invalid_key", message="x") == "ignored"
    assert _rows()[0] == 1


@pytest.mark.django_db
def test_a_failed_outcome_also_wins(version):
    assert store_setup_failure(agent_version_id=version.id, code="invalid_key", message="m" * 3000) == "failed"

    with pytest.raises(AgentStoreError) as raised:
        store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    assert raised.value.code == "setup_outcome_already_stored"
    outcome = setup_outcome(version.id)
    assert (outcome.status, outcome.failure_code, len(outcome.failure_message)) == ("failed", "invalid_key", 2000)


@pytest.mark.django_db
def test_setup_outcome_and_stored_agent_without_a_row(version):
    assert setup_outcome(version.id) is None
    assert stored_agent(version.id) is None

    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    outcome = setup_outcome(version.id)
    assert (outcome.status, outcome.failure_code, outcome.failure_message) == ("stored", "", "")


@pytest.mark.django_db
def test_stored_agent_hides_a_stale_hosted_agent(hosted_definition, hosted_version, provider_list):
    store_agent(agent_version_id=hosted_version.id, agent=hosted_agent(), sdk_version="0.2.3")
    AgentDefinition.objects.filter(id=hosted_definition.id).update(assistant_id="asst_new")

    assert stored_agent(hosted_version.id) is None
    assert stored_agent(hosted_version.id, include_stale=True)["provider_ref"]["id"] == "asst_1"


@pytest.mark.django_db
def test_a_source_agent_is_never_stale(definition, version):
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    AgentDefinition.objects.filter(id=definition.id).update(provider="livekit", assistant_id="x")

    assert stored_agent(version.id) is not None
    assert agent_summary(definition.id).setup_stale is False


@pytest.mark.django_db
def test_summary_of_a_stored_source_agent(definition, version):
    AgentDefinition.objects.filter(id=definition.id).update(provider="livekit")
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    summary = agent_summary(definition.id)

    assert summary.agent_stored is True
    assert (summary.kind, summary.type, summary.instrumentation) == ("source", "chat", "traceai")
    assert (summary.unit_ids, summary.setup_hash) == (("main",), HASH_A)
    assert (summary.provider, summary.provider_agent_id) == ("livekit", "")
    assert summary.agent_version_id == version.id


@pytest.mark.django_db
def test_summary_of_a_legacy_agent(definition, version):
    summary = agent_summary(definition.id)

    assert summary.agent_stored is False
    assert (summary.kind, summary.instrumentation, summary.setup_hash) == (None, "none", None)
    assert (summary.type, summary.unit_ids) == ("chat", ("main",))
    assert summary.missing == ("provider", "provider_agent_id", "credential")


@pytest.mark.django_db
def test_summary_of_a_failed_row_is_legacy(definition, version):
    store_setup_failure(agent_version_id=version.id, code="invalid_key", message="x")

    summary = agent_summary(definition.id)

    assert (summary.agent_stored, summary.kind) == (False, None)


@pytest.mark.django_db
def test_summary_falls_back_to_the_newest_stored_row(definition, version):
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    newer = definition.create_version(description="", commit_message="second", status="active")

    summary = agent_summary(definition.id)

    assert summary.agent_version_id == newer.id
    assert (summary.agent_stored, summary.setup_hash) == (True, HASH_A)


@pytest.mark.django_db
def test_summary_ignores_the_row_of_a_deleted_version(definition, version):
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    definition.create_version(description="", commit_message="second", status="active")
    version.delete()

    assert agent_summary(definition.id).agent_stored is False


@pytest.mark.django_db
def test_summary_of_a_stale_hosted_agent(hosted_definition, hosted_version, provider_list):
    store_agent(agent_version_id=hosted_version.id, agent=hosted_agent(), sdk_version="0.2.3")
    AgentDefinition.objects.filter(id=hosted_definition.id).update(assistant_id="asst_new")

    summary = agent_summary(hosted_definition.id)

    assert (summary.agent_stored, summary.setup_stale, summary.kind) == (False, True, None)
    assert summary.provider_agent_id == "asst_new"


@pytest.mark.django_db
def test_summary_finds_the_active_versions_key(hosted_definition, hosted_version):
    credential = ProviderCredentials.objects.create(
        agent_version=hosted_version, provider_type="vapi", api_key="sk-test", assistant_id="asst_1"
    )

    summary = agent_summary(hosted_definition.id)

    assert (summary.credential_id, summary.key_present, summary.missing) == (credential.id, True, ())


@pytest.mark.django_db
def test_summary_counts_a_plain_column_key(hosted_definition, hosted_version):
    AgentDefinition.objects.filter(id=hosted_definition.id).update(api_key="sk-plain")

    summary = agent_summary(hosted_definition.id)

    assert (summary.credential_id, summary.key_present) == (None, True)


@pytest.mark.django_db
def test_summary_of_an_unknown_definition():
    with pytest.raises(AgentNotFound):
        agent_summary(uuid.uuid4())


@pytest.mark.django_db
def test_summary_reads_in_at_most_ten_queries_and_writes_nothing(definition, version):
    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    with CaptureQueriesContext(connection) as queries:
        agent_summary(definition.id, agent_version_id=version.id)

    assert len(queries) <= 10
    assert not any(
        query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for query in queries
    )


@pytest.mark.django_db
def test_store_logs_the_outcome_without_the_document(version):
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
        with pytest.raises(AgentStoreError):
            store_agent(agent_version_id=version.id, agent=source_agent(setup_hash=HASH_B), sdk_version="0.2.3")

    events = [entry["event"] for entry in logs]
    assert events == ["agent_setup_stored", "agent_setup_rejected"]
    assert all("harbor_hotels" not in str(entry) and "booking assistant" not in str(entry) for entry in logs)


@pytest.mark.django_db
def test_versions_keep_no_part_of_the_agent(version):
    snapshot = copy.deepcopy(AgentVersion.objects.get(id=version.id).configuration_snapshot)

    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")

    assert AgentVersion.objects.get(id=version.id).configuration_snapshot == snapshot

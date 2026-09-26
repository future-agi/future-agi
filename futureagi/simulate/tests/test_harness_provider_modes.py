from types import SimpleNamespace

import pytest

from simulate.serializers.harness_job import (
    HarnessAgentSerializer,
    HarnessJobCreateSerializer,
)
from simulate.services.harness_provider import _validate_phone_connectivity
from simulate.services.phone_telephony import platform_phone_telephony


@pytest.mark.parametrize(
    "connector,target_key,alias",
    [
        ("vapi", "assistant_id", "VAPI_API_KEY"),
        ("retell", "agent_id", "RETELL_API_KEY"),
    ],
)
@pytest.mark.parametrize("fetch", [False, True])
def test_provider_telephony_allows_fetch_or_prompt_without_source(
    connector, target_key, alias, fetch
):
    config = {"phone_number": "+14155551234"}
    refs = {}
    if fetch:
        config[target_key] = "existing-agent"
        refs[alias] = {
            "manager": "platform-vault",
            "key": "test-ref",
            "purpose": "target_provider",
        }
    else:
        config["target_system_prompt"] = "You book rides."
    serializer = HarnessJobCreateSerializer(
        data={
            "schema_version": "futureagi.harness-job.v1",
            "agent": {
                "connector": connector,
                "mode": "connect_only",
                "config": config,
                "secret_refs": refs,
            },
            "scenario_count": 1,
            "artifacts": {"level": "full"},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["agent"]["connector"] == (
        connector if fetch else "phone"
    )
    assert serializer.validated_data["source"]["kind"] == "provider"


@pytest.mark.parametrize(
    "connector,target_key", [("vapi", "assistant_id"), ("retell", "agent_id")]
)
@pytest.mark.parametrize(
    "extra,mode",
    [
        ({}, "connect_only"),
        ({"phone_number": "invalid", "target_system_prompt": "prompt"}, "connect_only"),
        ({"sip_trunk_id": "customer-trunk"}, "connect_only"),
        ({}, "provider_import"),
        ({"target_id": "agent"}, "connect_only"),
    ],
)
def test_provider_telephony_rejects_invalid_inputs(connector, target_key, extra, mode):
    config = {"phone_number": "+14155551234", **extra}
    if "target_id" in config:
        config[target_key] = config.pop("target_id")
    serializer = HarnessAgentSerializer(
        data={"connector": connector, "mode": mode, "config": config}
    )
    assert not serializer.is_valid()


def test_vapi_connect_only_accepts_existing_assistant_id():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "connect_only",
            "config": {"assistant_id": "assistant-123"},
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["mode"] == "connect_only"


def test_phone_connect_only_accepts_number_and_prompt_without_provider_key():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "phone",
            "mode": "connect_only",
            "config": {
                "phone_number": "+14155551234",
                "target_system_prompt": "You help callers book appointments.",
            },
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors


def test_voice_call_behavior_accepts_only_boolean_values():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "livekit",
            "config": {"inbound": False, "target_speaks_first": True},
        }
    )
    assert serializer.is_valid(), serializer.errors

    for name in ("inbound", "target_speaks_first"):
        serializer = HarnessAgentSerializer(
            data={"connector": "livekit", "config": {name: "false"}}
        )
        assert not serializer.is_valid()
        assert "config" in serializer.errors


def test_phone_connect_only_rejects_invalid_number_or_missing_prompt():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "phone",
            "mode": "connect_only",
            "config": {"phone_number": "5551234"},
        }
    )
    assert not serializer.is_valid()


def test_phone_connect_only_rejects_customer_caller_id_override():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "phone",
            "mode": "connect_only",
            "config": {
                "phone_number": "+14155551234",
                "target_system_prompt": "You help callers book appointments.",
                "sip_outbound_from_number": "+14155559999",
            },
        }
    )
    assert not serializer.is_valid()


def test_phone_connect_only_needs_no_repository_or_customer_secret():
    serializer = HarnessJobCreateSerializer(
        data={
            "schema_version": "futureagi.harness-job.v1",
            "agent": {
                "connector": "phone",
                "mode": "connect_only",
                "config": {
                    "phone_number": "+14155551234",
                    "target_system_prompt": "You help callers book appointments.",
                },
                "secret_refs": {},
            },
            "scenario_count": 1,
            "artifacts": {"level": "full"},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"]["kind"] == "provider"


@pytest.mark.parametrize("connector", ["phone", "vapi", "retell"])
def test_phone_reuses_agent_definition_telephony(monkeypatch, connector):
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.example.com")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_OUTBOUND_TRUNK_ID", "ST_existing-outbound")
    monkeypatch.setenv("PSTN_CALLER_NUMBER", "+14155550123")
    # The older generic SIP setting may refer to an inbound trunk. The agent
    # definition's known-good outbound trunk must take precedence.
    monkeypatch.setenv("SIP_OUTBOUND_TRUNK_ID", "ST_other-direction")
    values = platform_phone_telephony()
    assert values["SIP_OUTBOUND_TRUNK_ID"] == "ST_existing-outbound"
    assert values["SIP_OUTBOUND_FROM_NUMBER"] == "+14155550123"
    _validate_phone_connectivity(
        {
            "agent": {
                "connector": connector,
                "mode": "connect_only",
                "config": {"phone_number": "+14155551234"},
            }
        }
    )


def test_retell_environment_backed_accepts_repository_lifecycle():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell",
            "mode": "environment_backed",
            "config": {"lifecycle_manifest": "config/alk.yaml"},
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors


def test_retell_chat_connect_only_accepts_agent_and_dynamic_variables():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell_chat",
            "mode": "connect_only",
            "config": {
                "agent_id": "chat-agent-123",
                "dynamic_variables": {"customer_name": "Jane", "balance": 124},
            },
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors


def test_retell_chat_rejects_environment_backed_mode():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell_chat",
            "mode": "environment_backed",
            "config": {},
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "mode" in serializer.errors


def test_environment_backed_rejects_existing_target_id():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "environment_backed",
            "config": {"assistant_id": "production-agent"},
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors


def test_vapi_provider_import_accepts_source_id_and_safe_routes():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "provider_import",
            "config": {
                "assistant_id": "assistant-123",
                "event_path": "/provider/events",
                "tool_path": "/provider/tools",
            },
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors


def test_retell_provider_import_requires_source_id():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell",
            "mode": "provider_import",
            "config": {},
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors


def test_provider_import_rejects_route_traversal():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "provider_import",
            "config": {
                "assistant_id": "assistant-123",
                "tool_path": "/provider/../admin",
            },
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors


@pytest.mark.parametrize("connector", ["livekit", "vapi", "retell", "auto"])
@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_call_direction_is_accepted_for_voice_connectors(connector, direction):
    config = {"agent_id": "agent-1"} if connector == "retell" else {}
    serializer = HarnessAgentSerializer(
        data={
            "connector": connector,
            "call_direction": direction,
            "config": config,
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["call_direction"] == direction


def test_call_direction_is_refused_for_a_chat_connector():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell_chat",
            "call_direction": "outbound",
            "config": {"agent_id": "agent-1"},
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "call_direction" in serializer.errors
    assert "retell_chat is chat" in str(serializer.errors["call_direction"])


def test_call_direction_accepts_only_the_two_directions():
    serializer = HarnessAgentSerializer(
        data={"connector": "livekit", "call_direction": "sideways", "config": {}}
    )
    assert not serializer.is_valid()
    assert "call_direction" in serializer.errors


def test_call_direction_may_be_null():
    serializer = HarnessAgentSerializer(
        data={"connector": "retell_chat", "call_direction": None, "config": {}}
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    "number,valid",
    [
        ("+123456", False),
        ("+1234567", True),
        ("+14155551234", True),
        ("+123456789012345", True),
        ("+1234567890123456", False),
        ("+0123456789", False),
        ("14155551234", False),
        ("+1 415 555 1234", False),
        (" +14155551234 ", True),
        ("", False),
    ],
)
def test_phone_target_numbers_must_be_e164(number, valid):
    serializer = HarnessAgentSerializer(
        data={
            "connector": "phone",
            "mode": "connect_only",
            "config": {"phone_number": number, "target_system_prompt": "You help."},
            "secret_refs": {},
        }
    )
    assert serializer.is_valid() is valid, serializer.errors
    if not valid:
        assert "E.164" in str(serializer.errors["config"])


@pytest.mark.parametrize(
    "connector,target_key", [("vapi", "assistant_id"), ("retell", "agent_id")]
)
@pytest.mark.parametrize("number,valid", [("+123456", False), ("+1234567", True)])
def test_connect_only_provider_numbers_use_the_same_rule(
    connector, target_key, number, valid
):
    serializer = HarnessAgentSerializer(
        data={
            "connector": connector,
            "mode": "connect_only",
            "config": {"phone_number": number, target_key: "agent-1"},
            "secret_refs": {
                ("VAPI_API_KEY" if connector == "vapi" else "RETELL_API_KEY"): {
                    "manager": "platform-vault",
                    "key": "test-ref",
                    "purpose": "target_provider",
                }
            },
        }
    )
    assert serializer.is_valid() is valid, serializer.errors


def test_phone_target_prompt_is_bounded():
    def build(prompt):
        return HarnessAgentSerializer(
            data={
                "connector": "phone",
                "mode": "connect_only",
                "config": {
                    "phone_number": "+14155551234",
                    "target_system_prompt": prompt,
                },
                "secret_refs": {},
            }
        )

    assert build("x" * 65_536).is_valid()
    assert not build("x" * 65_537).is_valid()
    assert not build("   ").is_valid()


@pytest.mark.parametrize("name", ["inbound", "target_speaks_first"])
@pytest.mark.parametrize(
    "value,valid",
    [(True, True), (False, True), ("yes", False), (1, False), (None, False)],
)
def test_call_behaviour_flags_must_be_booleans(name, value, valid):
    serializer = HarnessAgentSerializer(
        data={"connector": "livekit", "config": {name: value}, "secret_refs": {}}
    )
    assert serializer.is_valid() is valid, serializer.errors
    if not valid:
        assert f"{name} must be a boolean" in str(serializer.errors["config"])


def test_config_must_be_an_object():
    serializer = HarnessAgentSerializer(
        data={"connector": "livekit", "config": ["not", "an", "object"]}
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors


def test_phone_target_needs_no_provider_credential():
    from simulate.serializers.harness_job import missing_provider_credentials

    assert (
        missing_provider_credentials(
            {"connector": "phone", "mode": "connect_only", "secret_refs": {}}
        )
        == []
    )


def test_phone_target_without_a_source_is_a_valid_job():
    serializer = HarnessJobCreateSerializer(
        data={
            "schema_version": "futureagi.harness-job.v1",
            "agent": {
                "connector": "phone",
                "mode": "connect_only",
                "call_direction": "outbound",
                "config": {
                    "phone_number": "+14155551234",
                    "target_system_prompt": "You book rides.",
                },
                "secret_refs": {},
            },
            "scenario_count": 1,
            "artifacts": {"level": "full"},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"] == {
        "kind": "provider",
        "visibility": "public",
    }
    assert serializer.validated_data["agent"]["call_direction"] == "outbound"


@pytest.mark.django_db
class TestDeclaredDirectionWinsOverTheAuthoredGuess:
    def _job(self, organization, key, agent, authored):
        from simulate.services.hosted_harness import create_hosted_job

        from .test_hosted_harness_channels import _payload

        job, _ = create_hosted_job(
            organization, _payload(agent=agent), idempotency_key=key
        )
        job.stage_outputs = [{"kind": "contract", "data": authored}]
        job.save(update_fields=["stage_outputs", "updated_at"])
        return job

    def _definition(self, **fields):
        saved = []
        attributes = {
            "description": "",
            "provider": "",
            "agent_name": "alk-sdk-agent",
            "inbound": True,
            "target_speaks_first": False,
            "latest_version": object(),
            "save": lambda update_fields: saved.append(list(update_fields)),
            **fields,
        }
        return SimpleNamespace(**attributes), saved

    def test_submitted_direction_overrides_the_contract(self, organization):
        from simulate.services.hosted_harness import _record_target_agent_facts

        job = self._job(
            organization,
            "direction-declared",
            {"connector": "vapi", "call_direction": "outbound", "config": {}},
            {"call_direction": "inbound"},
        )
        definition, saved = self._definition()

        _record_target_agent_facts(job, definition, {})

        assert definition.inbound is False
        assert "inbound" in saved[0]

    def test_contract_direction_applies_when_none_was_submitted(self, organization):
        from simulate.services.hosted_harness import _record_target_agent_facts

        job = self._job(
            organization,
            "direction-authored",
            {"connector": "vapi", "config": {}},
            {"call_direction": "outbound"},
        )
        definition, saved = self._definition()

        _record_target_agent_facts(job, definition, {})

        assert definition.inbound is False

    def test_an_explicit_inbound_flag_beats_both(self, organization):
        from simulate.services.hosted_harness import _record_target_agent_facts

        job = self._job(
            organization,
            "direction-flag",
            {
                "connector": "vapi",
                "call_direction": "outbound",
                "config": {"inbound": True},
            },
            {"call_direction": "outbound"},
        )
        definition, _ = self._definition()

        _record_target_agent_facts(job, definition, {})

        assert definition.inbound is True

    def test_nothing_declared_leaves_the_definition_alone(self, organization):
        from simulate.services.hosted_harness import _record_target_agent_facts

        job = self._job(
            organization, "direction-none", {"connector": "livekit", "config": {}}, {}
        )
        definition, saved = self._definition(provider="livekit")

        _record_target_agent_facts(job, definition, {})

        assert definition.inbound is True
        assert saved == []

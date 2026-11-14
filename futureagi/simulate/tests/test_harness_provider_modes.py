from types import SimpleNamespace

import pytest

from simulate.serializers.harness_job import (
    HarnessAgentSerializer,
    HarnessJobCreateSerializer,
)
from simulate.services.harness_provider import _validate_phone_connectivity
from simulate.services.hosted_harness import _target_agent_prompt
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


LIVEKIT_TARGET_REFS = {
    alias: {
        "manager": "platform-vault",
        "key": f"ref-{alias}",
        "purpose": "target_provider",
    }
    for alias in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
}


def _livekit_job(config=None, mode="connect_only", refs=None):
    return HarnessJobCreateSerializer(
        data={
            "schema_version": "futureagi.harness-job.v1",
            "agent": {
                "connector": "livekit",
                "mode": mode,
                "config": {
                    "agent_name": "returns-agent",
                    "livekit_url": "wss://demo.livekit.cloud",
                    "target_system_prompt": "You handle returns.",
                    **(config or {}),
                },
                "secret_refs": LIVEKIT_TARGET_REFS if refs is None else refs,
            },
            "scenario_count": 1,
            "artifacts": {"level": "full"},
        }
    )


def test_livekit_connect_only_accepts_agent_name_and_prompt_without_source():
    serializer = _livekit_job()

    assert serializer.is_valid(), serializer.errors
    agent = serializer.validated_data["agent"]
    assert agent["connector"] == "livekit"
    assert agent["mode"] == "connect_only"
    assert serializer.validated_data["source"]["kind"] == "provider"


@pytest.mark.parametrize(
    "config,mode,message",
    [
        ({"agent_name": " "}, "connect_only", "agent_name is required"),
        (
            {"target_system_prompt": ""},
            "connect_only",
            "target_system_prompt is required",
        ),
        (
            {"target_system_prompt": "x" * 65537},
            "connect_only",
            "target_system_prompt is required",
        ),
        ({}, "provider_import", "LiveKit targets support connect_only only"),
        ({}, "environment_backed", "LiveKit targets support connect_only only"),
    ],
)
def test_livekit_hosted_target_rejects_incomplete_or_unsupported_input(
    config, mode, message
):
    serializer = _livekit_job(config=config, mode=mode)

    assert not serializer.is_valid()
    assert message in str(serializer.errors)


def test_livekit_hosted_target_requires_the_full_credential_family():
    serializer = _livekit_job(
        refs={"LIVEKIT_API_KEY": LIVEKIT_TARGET_REFS["LIVEKIT_API_KEY"]}
    )

    assert not serializer.is_valid()
    assert "LIVEKIT_API_SECRET" in str(serializer.errors)


def test_livekit_without_a_mode_still_needs_source_code():
    serializer = _livekit_job(mode=None)

    assert not serializer.is_valid()
    assert "hosted agent ID or phone number" in str(serializer.errors)


def _authored_job(connector, config):
    return SimpleNamespace(
        payload={"agent": {"connector": connector, "config": config}},
        stage_outputs=[
            {"kind": "contract", "data": {"system_prompt_excerpt": "Excerpt."}}
        ],
    )


@pytest.mark.parametrize("connector", ["phone", "livekit"])
def test_pasted_prompt_wins_over_the_authored_excerpt(connector):
    job = _authored_job(connector, {"target_system_prompt": " Full pasted prompt. "})

    assert _target_agent_prompt(job, {"agent_prompt": "Guest excerpt."}) == (
        "Full pasted prompt."
    )


def test_livekit_built_from_source_keeps_the_authored_prompt():
    job = _authored_job("livekit", {})

    assert _target_agent_prompt(job, {}) == "Excerpt."

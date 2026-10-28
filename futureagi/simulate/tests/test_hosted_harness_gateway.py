from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import tracemalloc
import uuid
import zlib
from contextlib import nullcontext
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from typing import NamedTuple
from unittest.mock import patch
from urllib.parse import quote, urlparse

import pytest
from django.test import override_settings
from django.utils import timezone

from simulate.models import HostedHarnessAttempt, HostedHarnessJob
from simulate.services.harness_capacity import SandboxCapacity
from simulate.services.harness_provider import serialize_job
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    record_cleanup,
    register_attempt,
    request_cancellation,
)
from simulate.services.hosted_harness_diagnostics import cached_attempt_redaction_values
from simulate.services.hosted_harness_gateway import (
    _ADJUSTMENTS_PATH,
    _SIMULATOR_SECRETS_PATH,
    HostedHarnessGateway,
    HostedSourceAcquirer,
    _authoring_archive_for,
    _authoring_ttl_seconds,
    _connector_egress_domains,
    _execution_ttl_seconds,
    _known_simulator_egress_inputs,
    _add_scoped_guest_pin_policy,
    _normalize_egress_domains,
    _platform_simulator_material,
    _provider_egress_domains,
    _provider_import_authoring_material,
    _resolved_egress_domains,
    _scenarios_cli_command,
    _validate_resolved_egress_domains,
    _webrtc_egress_cidrs,
    attach_platform_simulator_secret_refs,
    detect_source_connectors,
    detect_source_credentials,
    guest_failure_cause,
    pack_authoring_archive,
    prepare_dispatch_payload,
    resolve_authored_connector,
    resolve_platform_simulator_secrets,
)
from simulate.services.hosted_sandbox import (
    SandboxConflictError,
    SandboxNotFoundError,
    SandboxProviderError,
)


@pytest.fixture(autouse=True)
def _no_conversation_runtime(monkeypatch):
    """A launch asks for the conversation's harness; tests never start one."""
    started: list[str] = []
    monkeypatch.setattr(
        "simulate.tasks.hosted_harness_conversation.schedule_conversation_runtime",
        lambda conversation_id, _base_url: started.append(conversation_id),
    )
    return started


@pytest.fixture(autouse=True)
def _isolate_platform_simulator_environment(settings, monkeypatch):
    """Tests opt in explicitly instead of reading the developer machine's provider keys.

    Hosted runs require AgentCC, so every test starts with one configured; a test about its
    absence removes it.
    """
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {}
    for name in (
        "AGENTCC_BASE_URL",
        "ALK_HOSTED_AGENTCC_BASE_URL",
        "AGENTCC_INTERNAL_API_KEY",
        "AGENTCC_HARNESS_API_KEY",
        "ALK_HOSTED_AGENTCC_MODEL",
        "ALK_HARNESS",
        "ALK_HARNESS_MODEL",
        "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER",
        "ALK_CAB_GUEST_POC_PIN",
        "SIMULATOR_STT_PROVIDER",
        "SIMULATOR_TTS_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-virtual-key")


def test_private_pin_policy_is_phone_scoped_and_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("ALK_CAB_GUEST_POC_PIN", "7682")
    job = SimpleNamespace(
        organization_id="org-approved",
        payload={
            "agent": {
                "connector": "phone",
                "config": {"phone_number": "+15551234567"},
            }
        },
    )
    values = {}
    assert _add_scoped_guest_pin_policy(values, job) is True
    assert values == {
        "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER": "+15551234567",
        "ALK_CAB_GUEST_POC_PIN": "7682",
    }

    values.clear()
    job.organization_id = "org-other"
    assert _add_scoped_guest_pin_policy(values, job) is True
    assert values == {
        "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER": "+15551234567",
        "ALK_CAB_GUEST_POC_PIN": "7682",
    }

    values.clear()
    job.payload["agent"]["config"]["phone_number"] = "+15557654321"
    assert _add_scoped_guest_pin_policy(values, job) is False
    assert values == {}


def test_add_scenarios_carries_job_only_for_target_scoped_policy() -> None:
    scoped = _scenarios_cli_command(
        name="guest", count=12, guidance=[], include_job=True
    )
    generic = _scenarios_cli_command(name="guest", count=12, guidance=[])

    assert "--job /work/job.json" in scoped
    assert "--job /work/job.json" not in generic


def test_guest_failure_cause_preserves_legacy_runnable_entrypoint_blocker() -> None:
    assert guest_failure_cause(
        "runtime validation: attempt 5/5\n"
        "  - lookup_account: no runnable shipped entrypoint was identified; "
        "expose the real implementation as an importable callable or an HTTP service\n"
    ) == (
        "lookup_account: no runnable shipped entrypoint was identified; "
        "expose the real implementation as an importable callable or an HTTP service"
    )


def test_platform_simulator_material_uses_deployment_credentials_only(monkeypatch):
    monkeypatch.setenv("SIMULATOR_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("SIMULATOR_LLM_MODEL", "gemini-3.7-flash")
    monkeypatch.delenv("ALK_HARNESS", raising=False)
    monkeypatch.delenv("ALK_HARNESS_MODEL", raising=False)
    monkeypatch.setenv("ALK_HOSTED_AGENTCC_BASE_URL", "https://gateway.futureagi.test")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-key")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-key")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "platform-deepgram-secret")
    monkeypatch.setenv("CARTESIA_API_KEY", "platform-cartesia-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://platform-livekit.example")
    monkeypatch.setenv("LIVEKIT_API_KEY", "platform-livekit-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "platform-livekit-secret")
    monkeypatch.setenv("LIVEKIT_OUTBOUND_TRUNK_ID", "ST_platform-outbound")
    monkeypatch.setenv("PSTN_CALLER_NUMBER", "+14155550123")
    monkeypatch.setenv("ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("ALK_CAB_GUEST_POC_PIN", "7682")

    values = _platform_simulator_material()

    # Behind the gateway neither the models nor speech need a vendor credential in the sandbox.
    assert "GOOGLE_CLOUD_PROJECT" not in values
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in values
    assert "DEEPGRAM_API_KEY" not in values
    assert "CARTESIA_API_KEY" not in values
    assert values["LIVEKIT_URL"] == "wss://platform-livekit.example"
    assert values["LIVEKIT_API_KEY"] == "platform-livekit-key"
    assert values["LIVEKIT_API_SECRET"] == "platform-livekit-secret"
    assert values["SIP_OUTBOUND_TRUNK_ID"] == "ST_platform-outbound"
    assert values["SIP_OUTBOUND_FROM_NUMBER"] == "+14155550123"
    assert "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER" not in values
    assert "ALK_CAB_GUEST_POC_PIN" not in values
    assert values["ALK_HARNESS"] == "claude"
    assert values["ALK_HARNESS_MODEL"] == "vertex_ai/gemini-3.7-flash"
    assert values["ALK_CLAUDE_GATEWAY_URL"] == "https://gateway.futureagi.test"
    assert values["ALK_CLAUDE_GATEWAY_API_KEY"] == "harness-key"
    assert "ANTHROPIC_VERTEX_PROJECT_ID" not in values


def test_platform_simulator_defaults_to_gateway_authoring_and_gemini_caller(
    monkeypatch,
):
    monkeypatch.delenv("SIMULATOR_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("SIMULATOR_LLM_MODEL", raising=False)

    values = _platform_simulator_material()

    assert values["SIMULATOR_LLM_PROVIDER"] == "vertex"
    assert values["SIMULATOR_LLM_MODEL"] == "vertex_ai/gemini-3.8-flash"
    assert values["ALK_HARNESS"] == "claude"
    assert values["ALK_HARNESS_MODEL"] == "vertex_ai/gemini-3.7-flash"


def test_platform_authoring_backend_is_independent_from_simulated_caller(monkeypatch):
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "platform-internal-key")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "platform-harness-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("SIMULATOR_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("SIMULATOR_LLM_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("ALK_HARNESS_MODEL", "claude-sonnet-4-6")
    values = _platform_simulator_material()

    assert values["ALK_HARNESS"] == "claude"
    assert values["ALK_HARNESS_MODEL"] == "claude-sonnet-4-6"
    assert values["SIMULATOR_LLM_PROVIDER"] == "vertex"
    assert values["SIMULATOR_LLM_MODEL"] == "gemini-3.1-flash-lite"
    assert values["AGENTCC_API_KEY"] == "platform-harness-key"


def test_claude_authoring_prefers_platform_owned_harness_key(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("ALK_HARNESS_MODEL", "vertex_ai/gemini-3.7-flash")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-service-key")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-virtual-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")

    values = _platform_simulator_material()

    assert values["AGENTCC_API_KEY"] == "harness-virtual-key"
    assert values["AGENTCC_BASE_URL"] == "https://gateway.example.test"
    assert "gateway.example.test" in _resolved_egress_domains(
        {"agent": {"connector": "auto"}, "security": {"allowed_egress_domains": []}},
        {},
        values,
        None,
    )


def test_platform_ambience_clips_reach_the_harness_and_its_egress(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-service-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
    monkeypatch.delenv("ALK_BACKGROUND_NOISE_CATALOG", raising=False)

    values = _platform_simulator_material()

    clips = json.loads(values["ALK_BACKGROUND_NOISE_CATALOG"])
    assert clips and all(
        clip["environment"] and clip["url"].startswith("https://") for clip in clips
    )
    domains = _resolved_egress_domains(
        {"agent": {"connector": "auto"}, "security": {"allowed_egress_domains": []}},
        {},
        values,
        None,
    )
    assert {urlparse(clip["url"]).hostname for clip in clips} <= domains


def test_caller_barge_in_rate_comes_from_platform_configuration(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "gemini")
    monkeypatch.setenv("HARNESS_CALLER_BARGE_IN_RATE", "0.2")
    values = _platform_simulator_material()
    assert values["HARNESS_CALLER_BARGE_IN_RATE"] == "0.2"


def test_a_deployment_catalogue_overrides_the_platform_clips(monkeypatch, tmp_path):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-service-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
    inline = '[{"environment":"street","url":"https://clips.example.test/street.mp3"}]'
    monkeypatch.setenv("ALK_BACKGROUND_NOISE_CATALOG", inline)

    values = _platform_simulator_material()

    assert values["ALK_BACKGROUND_NOISE_CATALOG"] == inline

    path = tmp_path / "clips.json"
    path.write_text(inline, encoding="utf-8")
    monkeypatch.setenv("ALK_BACKGROUND_NOISE_CATALOG", str(path))

    values = _platform_simulator_material()

    assert values["ALK_BACKGROUND_NOISE_CATALOG"] == inline


def test_admission_counts_the_ambience_hosts_that_launch_adds(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-service-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")
    monkeypatch.delenv("ALK_BACKGROUND_NOISE_CATALOG", raising=False)
    payload = {
        "agent": {"connector": "auto"},
        "security": {"allowed_egress_domains": []},
    }

    launched = _platform_simulator_material()
    clip_hosts = {
        urlparse(clip["url"]).hostname
        for clip in json.loads(launched["ALK_BACKGROUND_NOISE_CATALOG"])
    }

    assert clip_hosts <= _resolved_egress_domains(
        payload, {}, _known_simulator_egress_inputs(), None
    )


def test_claude_authoring_uses_separate_remote_gateway_key(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("ALK_HARNESS_MODEL", "vertex_ai/gemini-3.7-flash")
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "local-internal-key")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "remote-virtual-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.futureagi.com")

    values = _platform_simulator_material()

    assert values["AGENTCC_API_KEY"] == "remote-virtual-key"
    assert values["AGENTCC_BASE_URL"] == "https://gateway.futureagi.com"


def test_hosted_runs_require_sandbox_reachable_gateway(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.setenv("AGENTCC_HARNESS_API_KEY", "harness-virtual-key")
    monkeypatch.delenv("AGENTCC_BASE_URL", raising=False)

    with pytest.raises(HostedHarnessError) as exc:
        _platform_simulator_material()

    assert exc.value.code == "agentcc_not_configured"


def test_claude_authoring_falls_back_to_internal_key(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    monkeypatch.delenv("AGENTCC_HARNESS_API_KEY", raising=False)
    monkeypatch.setenv("AGENTCC_INTERNAL_API_KEY", "internal-evaluator-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test")

    values = _platform_simulator_material()

    assert values["AGENTCC_API_KEY"] == "internal-evaluator-key"
    assert values["ALK_CLAUDE_GATEWAY_API_KEY"] == "internal-evaluator-key"


def test_provider_egress_includes_vertex_auth_and_both_model_regions():
    domains = _provider_egress_domains(
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": "<encrypted-at-rest>",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        }
    )

    assert domains == {
        "aiplatform.googleapis.com",
        "oauth2.googleapis.com",
        "us-central1-aiplatform.googleapis.com",
        "us-east5-aiplatform.googleapis.com",
    }


def test_provider_egress_includes_scoped_claude_gateway():
    assert _provider_egress_domains(
        {
            "ALK_CLAUDE_GATEWAY_URL": "https://gateway.futureagi.com",
            "ALK_CLAUDE_GATEWAY_API_KEY": "opaque",
        }
    ) == {"gateway.futureagi.com"}


def test_provider_egress_includes_vapi_and_retell_call_hosts():
    assert _provider_egress_domains({"VAPI_API_KEY": "opaque"}) == {
        "*.vapi.ai",
    }


def test_webrtc_cidrs_apply_only_to_voice_connectors(settings):
    settings.ALK_HOSTED_WEBRTC_EGRESS_CIDRS = [
        "143.223.88.0/21",
        "216.39.248.0/21",
    ]

    assert _webrtc_egress_cidrs({"agent": {"connector": "retell"}}, {}) == (
        "143.223.88.0/21",
        "216.39.248.0/21",
    )
    assert _webrtc_egress_cidrs({"agent": {"connector": "retell_chat"}}, {}) == ()


def test_execution_ttl_preserves_authoring_and_call_budget(settings):
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200

    assert _execution_ttl_seconds({"max_duration_seconds": 600}) == 7200
    assert _execution_ttl_seconds({"max_duration_seconds": 5000}) == 8720


def test_daytona_authoring_ttl_preserves_legacy_forty_minute_envelope(settings):
    settings.ALK_HOSTED_AUTHORING_TIMEOUT = 11_100

    assert _authoring_ttl_seconds("daytona") == 2400
    assert _authoring_ttl_seconds("e2b") == 11_100


def test_provider_import_authoring_gets_only_the_matching_target_key():
    job = SimpleNamespace()
    payload = {"agent": {"connector": "retell", "mode": "provider_import"}}
    with patch(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        return_value={
            "RETELL_API_KEY": "retell-target-secret",
            "UNRELATED_AGENT_SECRET": "must-not-travel",
        },
    ):
        material, connector = _provider_import_authoring_material(job, payload)

    assert connector == "retell"
    assert material == {"RETELL_API_KEY": "retell-target-secret"}
    assert _provider_egress_domains({"RETELL_API_KEY": "opaque"}) == {
        "api.retellai.com",
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
    }


def test_platform_simulator_secrets_are_namespaced_and_not_request_controlled(
    settings, monkeypatch, tmp_path
):
    adc = tmp_path / "vertex.json"
    adc.write_text('{"project_id":"futureagi"}', encoding="utf-8")
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {
        "SIMULATOR_DEEPGRAM_API_KEY": "DEEPGRAM_API_KEY",
        "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": (
            "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        ),
    }
    monkeypatch.setenv("DEEPGRAM_API_KEY", "platform-deepgram")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))

    resolved = resolve_platform_simulator_secrets()

    assert resolved == {
        "SIMULATOR_DEEPGRAM_API_KEY": "platform-deepgram",
        "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": '{"project_id":"futureagi"}',
    }


def test_platform_livekit_is_available_only_as_simulator_configuration(
    settings, monkeypatch
):
    settings.ALK_HOSTED_SIMULATOR_SECRET_ENV = {
        "SIMULATOR_LIVEKIT_URL": "LIVEKIT_URL",
        "SIMULATOR_LIVEKIT_API_KEY": "LIVEKIT_API_KEY",
        "SIMULATOR_LIVEKIT_API_SECRET": "LIVEKIT_API_SECRET",
    }
    monkeypatch.setenv("LIVEKIT_URL", "wss://platform-livekit.example")
    monkeypatch.setenv("LIVEKIT_API_KEY", "platform-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "platform-secret")

    assert resolve_platform_simulator_secrets() == {
        "SIMULATOR_LIVEKIT_URL": "wss://platform-livekit.example",
        "SIMULATOR_LIVEKIT_API_KEY": "platform-key",
        "SIMULATOR_LIVEKIT_API_SECRET": "platform-secret",
    }


def test_platform_simulator_refs_are_ephemeral_value_free_and_do_not_mutate_payload():
    payload = _payload()
    original = json.loads(json.dumps(payload))

    dispatched = attach_platform_simulator_secret_refs(
        payload,
        {"SIMULATOR_DEEPGRAM_API_KEY": "must-never-appear"},
    )

    assert payload == original
    assert dispatched["agent"]["secret_refs"]["SIMULATOR_DEEPGRAM_API_KEY"] == {
        "manager": "platform-config",
        "key": "SIMULATOR_DEEPGRAM_API_KEY",
        "purpose": "simulator_provider",
    }
    assert "must-never-appear" not in json.dumps(dispatched)


def test_resolved_egress_rejects_daytona_domain_overflow():
    with pytest.raises(
        HostedHarnessError, match="selected sandbox provider supports at most 20"
    ):
        _validate_resolved_egress_domains(
            {f"provider-{index}.example.com" for index in range(21)}
        )


def test_normalize_egress_domains_wildcard_coverage_keeps_apex():
    assert _normalize_egress_domains(
        [
            " Foo.Example.COM. ",
            "foo.example.com",
            "*.EXAMPLE.com.",
            "example.com.",
            "bar.other.test.",
        ]
    ) == {"*.example.com", "example.com", "bar.other.test"}


@pytest.mark.parametrize(
    ("alias", "domain"),
    [
        ("DEEPGRAM_API_KEY", "api.deepgram.com"),
        ("SIMULATOR_DEEPGRAM_API_KEY", "api.deepgram.com"),
        ("CARTESIA_API_KEY", "api.cartesia.ai"),
        ("OPENAI_API_KEY", "api.openai.com"),
        ("ANTHROPIC_API_KEY", "api.anthropic.com"),
    ],
)
def test_provider_egress_includes_direct_and_simulator_aliases(alias, domain):
    assert _provider_egress_domains({alias: "configured"}) == {domain}


def test_provider_egress_uses_selected_simulator_audio_provider():
    domains = _provider_egress_domains(
        {
            "SIMULATOR_DEEPGRAM_API_KEY": "configured",
            "SIMULATOR_CARTESIA_API_KEY": "configured",
            "SIMULATOR_STT_PROVIDER": "deepgram",
            "SIMULATOR_TTS_PROVIDER": "deepgram",
        }
    )

    assert domains == {"api.deepgram.com"}


def test_provider_egress_uses_selected_simulator_llm_provider():
    domains = _provider_egress_domains(
        {
            "SIMULATOR_GEMINI_API_KEY": "configured",
            "SIMULATOR_OPENAI_API_KEY": "configured",
            "SIMULATOR_LLM_PROVIDER": "openai",
        }
    )

    assert domains == {"api.openai.com"}


def test_livekit_cloud_connector_egress_is_wildcard_minimized(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"livekit_url": "wss://FOO.livekit.cloud."},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _resolved_egress_domains(payload, {}, {}, None) == {
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
    }


def test_self_hosted_livekit_connector_only_adds_signal_host(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"LIVEKIT_URL": "wss://voice.customer.example"},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(payload, {}) == {"voice.customer.example"}


def test_livekit_secret_url_takes_precedence_over_configured_url():
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"livekit_url": "wss://config.example.test"},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(
        payload, {"LIVEKIT_URL": "wss://resolved.example.test"}
    ) == {"resolved.example.test"}


def test_vapi_connector_adds_static_and_configured_endpoint_hosts():
    payload = {
        "agent": {
            "connector": "vapi",
            "config": {
                "api_base_url": "https://proxy.example.test",
                "websocketCallUrl": "wss://call.example.test/socket",
            },
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(payload, {}) == {
        "*.vapi.ai",
        "call.example.test",
        "proxy.example.test",
    }


@pytest.mark.parametrize("connector", ["vapi", "livekit"])
def test_vapi_call_control_hosts_survive_resolved_restricted_egress(
    settings, connector
):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {"connector": connector, "config": {}},
        "security": {"allowed_egress_domains": ["api.vapi.ai"]},
    }
    domains = _resolved_egress_domains(payload, {"VAPI_API_KEY": "opaque"})
    assert domains == {"*.vapi.ai"}
    _validate_resolved_egress_domains(domains)
    control_host = "aws-us-west-2-production1-phone-call-websocket.vapi.ai"
    assert any(
        domain.startswith("*.") and control_host.endswith(domain[1:])
        for domain in domains
    )
    assert "*" not in domains


def test_livekit_futureagi_eu_connector_adds_coturn_host():
    payload = {
        "agent": {
            "connector": "livekit",
            "config": {"livekit_url": "wss://livekit-eu.futureagi.com"},
        },
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(payload, {}) == {
        "coturn.turn-eu.futureagi.com",
        "livekit-eu.futureagi.com",
        "turn-eu.futureagi.com",
    }


def test_retell_connector_includes_default_livekit_cloud_egress(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.RETELL_LIVEKIT_URL = "wss://retell-ai.example.livekit.cloud"
    payload = {
        "agent": {"connector": "retell", "config": {}},
        "security": {"allowed_egress_domains": []},
    }

    assert _resolved_egress_domains(payload, {}, {}, None) == {
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
        "api.retellai.com",
    }


def test_retell_job_allowlists_platform_simulator_livekit_signaling_and_turn(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    settings.RETELL_LIVEKIT_URL = "wss://retell-ai.example.livekit.cloud"
    payload = {
        "agent": {"connector": "retell", "config": {}},
        "security": {"allowed_egress_domains": []},
    }
    simulator_env = {"LIVEKIT_URL": "wss://livekit-eu.futureagi.com"}

    assert _resolved_egress_domains(payload, {}, simulator_env, None) == {
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
        "api.retellai.com",
        "livekit-eu.futureagi.com",
        "coturn.turn-eu.futureagi.com",
        "turn-eu.futureagi.com",
    }


def test_livekit_job_does_not_spend_egress_slots_on_platform_simulator_livekit(
    settings,
):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {"connector": "livekit", "config": {}},
        "security": {"allowed_egress_domains": []},
    }
    simulator_env = {"LIVEKIT_URL": "wss://livekit-eu.futureagi.com"}

    assert _resolved_egress_domains(
        payload, {"LIVEKIT_URL": "wss://tenant-abc.livekit.cloud"}, simulator_env, None
    ) == {"*.livekit.cloud", "*.turn.livekit.cloud"}


def test_auto_connector_resolves_livekit_and_adds_cloud_turn_edges():
    payload = {
        "agent": {"connector": "auto", "config": {}},
        "security": {"allowed_egress_domains": []},
    }

    assert _connector_egress_domains(
        payload, {"LIVEKIT_URL": "wss://tenant-abc.livekit.cloud"}
    ) == {
        "tenant-abc.livekit.cloud",
        "*.livekit.cloud",
        "*.turn.livekit.cloud",
    }


def test_resolved_egress_cap_applies_after_wildcard_minimization(settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    payload = {
        "agent": {"connector": "auto", "config": {}},
        "security": {
            "allowed_egress_domains": [
                "*.example.com",
                "foo.example.com",
                *[f"domain-{index}.example.test" for index in range(19)],
            ]
        },
    }

    domains = _resolved_egress_domains(payload, {}, {}, None)
    assert "foo.example.com" not in domains
    assert "example.com" not in domains
    assert len(domains) == 20
    _validate_resolved_egress_domains(domains)


def _payload():
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "future-agi/reference-agent",
            "ref": "main",
            "commit_sha": "a" * 40,
            "visibility": "private",
            "installation_id": "installation-1",
        },
        "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
        "scenario_count": 1,
        "seed": 1,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 2,
            "memory_mb": 4096,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["agent.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure", "connectivity"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": True,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }


def test_authoring_archive_resolves_uploaded_source_by_explicit_key(tmp_path, settings):
    root = tmp_path / "authoring"
    scenario = root / "uploaded-agent" / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    stale = root / "uploaded-agent" / "webrtc-runs" / "old" / "postgres"
    stale.mkdir(parents=True)
    (stale / "pg_wal").write_bytes(b"must-not-upload")
    settings.ALK_HOSTED_BUNDLE_DIR = str(root)
    job = SimpleNamespace(
        payload={
            "source": {
                "kind": "archive",
                "archive_artifact_id": "0f95b074-d3db-47a1-a3a1-2e37991aa946",
            },
            "metadata": {"authoring_key": "uploaded-agent"},
        }
    )

    archive = _authoring_archive_for(job)

    assert archive is not None
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        assert "scenarios/one/scenario.json" in tar.getnames()
        assert "webrtc-runs/old/postgres/pg_wal" not in tar.getnames()


def test_fresh_authoring_archive_contains_contract_and_scenarios_only(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"agent":"ride"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    ignored = root / "webrtc-runs" / "old"
    ignored.mkdir(parents=True)
    (ignored / "recording.wav").write_bytes(b"not an authoring input")

    body = pack_authoring_archive(root)

    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        assert sorted(archive.getnames()) == [
            "contract.json",
            "scenarios/one/scenario.json",
        ]


def test_fresh_authoring_archive_carries_generic_certification_sidecars(tmp_path):
    scenario = tmp_path / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (tmp_path / "contract.json").write_text('{"agent":"ride"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    (tmp_path / "runtime-validation.json").write_text(
        '{"status":"certified"}', encoding="utf-8"
    )
    evidence = tmp_path / "generic-harness"
    evidence.mkdir()
    (evidence / "certification.json").write_text(
        '{"status":"certified"}', encoding="utf-8"
    )

    body = pack_authoring_archive(tmp_path)

    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        assert "runtime-validation.json" in archive.getnames()
        assert "generic-harness/certification.json" in archive.getnames()


def test_authoring_stage_outputs_exposes_generic_certification_evidence():
    from simulate.services.hosted_harness_gateway import (
        authoring_stage_outputs_from_archive,
    )

    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as archive:
        for name, value in {
            "generic-harness/certification.json": {
                "status": "certified",
                "fingerprint": "sha256:test",
            },
            "generic-harness/repair-history.json": {
                "results": [{"outcome": "applied"}]
            },
            "generic-harness/action-certification.json": {
                "actions": [{"action": "lookup", "status": "passed"}]
            },
        }.items():
            payload = json.dumps(value).encode()
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    outputs = authoring_stage_outputs_from_archive(body.getvalue())

    certification = next(item for item in outputs if item["kind"] == "certification")
    assert certification["summary"] == "certified · 1 repairs · 1 action probes"
    assert certification["data"]["certificate"]["fingerprint"] == "sha256:test"


def test_fresh_authoring_archive_rejects_missing_scenarios(tmp_path):
    (tmp_path / "contract.json").write_text("{}", encoding="utf-8")

    with pytest.raises(HostedHarnessError, match="without scenario artifacts"):
        pack_authoring_archive(tmp_path)


@pytest.mark.django_db
def test_unified_progress_freezes_authoring_for_saved_reruns(organization, monkeypatch):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="freeze-unified-authoring"
    )
    attempt = SimpleNamespace(id="attempt-1", job_id=job.id, attempt_number=1)
    files = {
        "/work/authoring/contract.json": b'{"modality":"voice"}',
        "/work/authoring/environment-bundle/environment-plan.json": b'{"runtime":{}}',
        "/work/authoring/scenarios.json": b'[{"scenario_key":"one"}]',
        "/work/bundle/manifest.json": b'{"schema_version":"v2"}',
        "/tmp/authoring-rerun.tar.gz": b"frozen-authoring",
    }
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(download_file=lambda path, timeout=None: files[path]),
        process=SimpleNamespace(
            exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result="")
        ),
    )

    monkeypatch.setattr(
        "simulate.services.harness_usage.record_sandbox_runtime",
        lambda *args, **kwargs: None,
    )
    with patch(
        "simulate.services.hosted_harness_gateway.store_authoring_archive"
    ) as store:
        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

    store.assert_called_once_with(job, b"frozen-authoring", advance_lifecycle=False)


def test_voice_authoring_resolves_auto_connector_with_livekit_credentials(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"modality":"voice"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    payload = _payload()
    payload["agent"] = {
        "connector": "auto",
        "config": {},
        "secret_refs": {
            "LIVEKIT_URL": {},
            "LIVEKIT_API_KEY": {},
            "LIVEKIT_API_SECRET": {},
        },
    }

    resolved = resolve_authored_connector(payload, pack_authoring_archive(root))

    assert resolved["agent"]["connector"] == "livekit"
    assert payload["agent"]["connector"] == "auto"


def test_authored_connector_never_overrides_explicit_or_ambiguous_input(tmp_path):
    root = tmp_path / "authoring"
    scenario = root / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (root / "contract.json").write_text('{"modality":"voice"}', encoding="utf-8")
    (scenario / "scenario.json").write_text('{"name":"one"}', encoding="utf-8")
    body = pack_authoring_archive(root)
    explicit = _payload()
    explicit["agent"]["connector"] = "retell"
    ambiguous = _payload()
    ambiguous["agent"]["connector"] = "auto"

    assert resolve_authored_connector(explicit, body)["agent"]["connector"] == "retell"
    assert resolve_authored_connector(ambiguous, body)["agent"]["connector"] == "auto"


def _source_tarball(files: dict[str, str]) -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"source/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive.getvalue()


def test_source_scan_reads_manifests_and_code_but_not_prose():
    detected, scanned = detect_source_connectors(
        _source_tarball(
            {
                "pyproject.toml": '[project]\ndependencies = ["livekit-agents>=1.0"]\n',
                "README.md": "We retell every booking back to the rider.",
                "node_modules/@vapi-ai/web/index.js": "export const vapi = 1;",
                "app/tools.py": "VAPID_PUBLIC_KEY = 'push'\n",
            }
        )
    )

    assert detected == ["livekit"]
    assert scanned == 2
    assert detect_source_connectors(b"not a tarball") == ([], 0)


def test_source_scan_detects_explicit_vertex_adc_requirement():
    connectors, required_files, scanned = detect_source_credentials(
        _source_tarball(
            {
                "agent.py": (
                    "import os\n"
                    "credentials = os.environ['GOOGLE_APPLICATION_CREDENTIALS']\n"
                ),
                "requirements.txt": "google-cloud-aiplatform==1.71.1\n",
            }
        )
    )

    assert connectors == []
    assert required_files == ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    assert scanned == 2


def test_source_scan_does_not_require_adc_for_google_sdk_or_api_key_mode():
    connectors, required_files, scanned = detect_source_credentials(
        _source_tarball(
            {
                "agent.py": (
                    "from google import genai\n"
                    "client = genai.Client(api_key=os.environ['GOOGLE_API_KEY'])\n"
                ),
                "README.md": "Set GOOGLE_APPLICATION_CREDENTIALS for Vertex mode.\n",
            }
        )
    )

    assert connectors == []
    assert required_files == []
    assert scanned == 1
    assert detect_source_credentials(b"not a tarball") == ([], [], 0)


def test_dispatch_payload_mirrors_only_livekit_url():
    payload = {"agent": {"connector": "livekit", "config": {}}}

    dispatched = prepare_dispatch_payload(
        payload,
        {
            "LIVEKIT_URL": "wss://customer.livekit.cloud",
            "LIVEKIT_API_KEY": "must-not-be-copied",
            "LIVEKIT_API_SECRET": "must-not-be-copied",
        },
    )

    assert dispatched["agent"]["config"] == {
        "livekit_url": "wss://customer.livekit.cloud"
    }
    assert dispatched["metadata"]["environment_value_names"] == [
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_URL",
    ]
    assert dispatched["metadata"]["generic_harness_v1"] is True
    assert payload["agent"]["config"] == {}
    assert "must-not-be-copied" not in json.dumps(dispatched)


def test_dispatch_payload_declares_resolved_adc_names_without_values():
    payload = {
        "agent": {"connector": "auto", "config": {}},
        "metadata": {"environment_value_names": ["MODEL_NAME"]},
    }

    dispatched = prepare_dispatch_payload(
        payload,
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": "must-not-be-copied",
            "GOOGLE_CLOUD_PROJECT": "futureagi",
        },
    )

    assert dispatched["metadata"]["environment_value_names"] == [
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "GOOGLE_CLOUD_PROJECT",
        "MODEL_NAME",
    ]
    assert dispatched["metadata"]["generic_harness_v1"] is True
    assert "must-not-be-copied" not in json.dumps(dispatched)
    assert payload["metadata"] == {"environment_value_names": ["MODEL_NAME"]}


@pytest.mark.parametrize("connector", ["vapi", "retell"])
def test_dispatch_phone_keeps_platform_dialer_out_of_target_config(connector):
    payload = {
        "agent": {"connector": connector, "config": {"phone_number": "+12345162722"}}
    }
    dispatched = prepare_dispatch_payload(
        payload, {}, simulator_secrets={"LIVEKIT_URL": "wss://platform.example"}
    )
    assert dispatched["agent"]["config"] == payload["agent"]["config"]
    assert "livekit_url" not in dispatched["agent"]["config"]


@pytest.mark.parametrize("connector", ["vapi", "retell"])
def test_dispatch_payload_uses_platform_livekit_url_for_provider_voice(connector):
    payload = {"agent": {"connector": connector, "config": {}}}

    dispatched = prepare_dispatch_payload(
        payload,
        {"VAPI_API_KEY": "target-only"},
        simulator_secrets={
            "LIVEKIT_URL": "wss://platform-livekit.example",
            "LIVEKIT_API_KEY": "must-not-be-copied",
            "LIVEKIT_API_SECRET": "must-not-be-copied",
        },
    )

    assert dispatched["agent"]["config"] == {
        "livekit_url": "wss://platform-livekit.example"
    }
    assert "must-not-be-copied" not in json.dumps(dispatched)


@pytest.mark.django_db
def test_gateway_clones_exact_commit_without_archiving_git_credentials(
    organization, monkeypatch
):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="source-gateway"
    )
    observed_commands = []
    observed_token = []

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.GitHubAppTokenProvider.from_settings",
        lambda: SimpleNamespace(
            credential=lambda _: nullcontext("installation-secret-token")
        ),
    )

    def run(command, **kwargs):
        observed_commands.append(command)
        if kwargs.get("env", {}).get("GIT_CONFIG_VALUE_0"):
            observed_token.append(kwargs["env"]["GIT_CONFIG_VALUE_0"])
        if command[:2] == ["git", "init"]:
            checkout = command[-1]
            from pathlib import Path

            path = Path(checkout)
            path.mkdir(parents=True)
            (path / ".git").mkdir()
            (path / "agent.py").write_text("print('agent')", encoding="utf-8")
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("simulate.services.hosted_harness_gateway.subprocess.run", run)
    archive, commit_sha = HostedSourceAcquirer().acquire(job)

    assert commit_sha == "a" * 40
    assert all(
        "installation-secret-token" not in " ".join(cmd) for cmd in observed_commands
    )
    assert observed_token == ["Authorization: Bearer installation-secret-token"] * 4
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert "source/agent.py" in names
    assert all(".git" not in name for name in names)


class _Filesystem:
    def __init__(self):
        self.uploads = {}

    def upload_file(self, content, path):
        self.uploads[path] = content

    def download_file(self, path, timeout=None):
        return b"command-1"


class _Process:
    def __init__(self):
        self.exec_calls = []
        self.sessions = []
        self.command_exit_code = None
        self.command_status = "running"
        self.entrypoint_output = ""
        self.process_output = ""

    def exec(self, command, **kwargs):
        self.exec_calls.append(command)
        result = self.process_output if "find /work/authoring" in command else ""
        return SimpleNamespace(exit_code=0, result=result)

    def create_session(self, session_id):
        self.sessions.append(session_id)

    def execute_session_command(self, session_id, request):
        self.session_request = request
        return SimpleNamespace(cmd_id="command-1")

    def get_session_command(self, session_id, command_id, request_timeout=None):
        return SimpleNamespace(
            exit_code=self.command_exit_code,
            status=self.command_status,
        )

    def get_session_command_logs(self, session_id, command_id, request_timeout=None):
        return SimpleNamespace(output=self.entrypoint_output)


class _Sandbox:
    def __init__(self):
        self.id = "sandbox-1"
        self.fs = _Filesystem()
        self.process = _Process()


class _Daytona:
    name = "daytona"
    runtime_name = "alk-hosted-v1"
    runtime_digest = ""
    max_egress_domains = 20
    create_timeout_seconds = 300
    supports_adjustments = True

    def __init__(self):
        self.sandbox = _Sandbox()
        self.params = None
        self.deleted = False
        self.lifecycle = []

    def create(self, params, **kwargs):
        self.params = params
        return self.sandbox

    def renew_ttl(self, sandbox, ttl_seconds):
        self.lifecycle.append("renew_ttl")

    def get(self, sandbox_id, request_timeout=None):
        if self.deleted:
            raise SandboxNotFoundError("sandbox not found", status_code=404)
        return self.sandbox

    def delete(self, sandbox, **kwargs):
        self.lifecycle.append("delete")
        self.deleted = True


def test_authoring_launch_uses_requested_resources(monkeypatch):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job = SimpleNamespace(id="job-1", payload=payload)
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._mark_stage", lambda *_: None
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        lambda *_: (b"source", None),
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._provider_import_authoring_material",
        lambda *_: ({}, ""),
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: {},
    )

    def capture_launch(spec, **_kwargs):
        assert (spec.cpu_units, spec.memory_mb, spec.disk_gb) == (2, 4096, 10)
        assert {"pypi.org", "files.pythonhosted.org"} <= set(spec.allowed_domains)
        raise RuntimeError("stop after resource admission")

    client.create = capture_launch
    with pytest.raises(RuntimeError, match="stop after resource admission"):
        gateway.author(job)


class _FailingDaytonaCreate(_Daytona):
    def create(self, params, **kwargs):
        self.params = params
        raise TimeoutError("sandbox create timed out")


class _ForbiddenDaytonaCreate(_Daytona):
    def create(self, params, **kwargs):
        self.params = params
        error = RuntimeError(
            "Failed to create sandbox: Organization is suspended: Depleted credits"
        )
        error.status_code = 403
        raise error


def test_offline_delivery_replays_durable_guest_spool(monkeypatch):
    artifact = b"result body"
    digest = __import__("hashlib").sha256(artifact).hexdigest()
    files = {
        f"outbound-spool/artifacts/{digest}.bin": artifact,
        f"outbound-spool/artifacts/{digest}.json": json.dumps(
            {
                "digest": digest,
                "kind": "result",
                "size": len(artifact),
                "content_type": "application/json",
                "scenario_key": "one",
            }
        ).encode(),
        "outbound-spool/events.spool.jsonl": (
            b'{"sequence":1,"type":"terminal","stage":"completed"}\n'
        ),
        "outbound-spool/receipts/receipt.json": b'{"digest":"receipt","scenario_key":"one"}',
        "outbound-spool/manifest.json": b'{"digest":"manifest"}',
    }
    archive_body = io.BytesIO()
    with tarfile.open(fileobj=archive_body, mode="w:gz") as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))

    sandbox = _Sandbox()
    sandbox.fs.download_file_stream = lambda path, timeout=None: (
        archive_body.getvalue()[offset : offset + 64]
        for offset in range(0, len(archive_body.getvalue()), 64)
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(get=lambda *args, **kwargs: sandbox)
    attempt = SimpleNamespace(
        id="attempt-1",
        provider_ref="sandbox-1",
        job=SimpleNamespace(max_artifact_bytes=1024 * 1024),
    )
    replayed = []
    recovered_receipt_options = []

    def artifact_ingest(*args, **kwargs):
        replayed.append(("artifact", kwargs["stream"].read()))

    def receipt_ingest(*args, **kwargs):
        replayed.append(("receipt", args[1]))
        recovered_receipt_options.append(kwargs)

    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_artifact",
        artifact_ingest,
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_event_batch",
        lambda *args, **kwargs: replayed.append(("events", args[1])),
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_result_receipt",
        receipt_ingest,
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_manifest",
        lambda *args, **kwargs: replayed.append(("manifest", args[1])),
    )

    assert gateway._recover_offline_delivery(attempt) is True
    assert recovered_receipt_options[0]["recovered_artifact_ids"] == [digest]
    assert recovered_receipt_options[0]["digest_body"] == {
        "digest": "receipt",
        "scenario_key": "one",
    }
    assert [kind for kind, _ in replayed] == [
        "artifact",
        "events",
        "receipt",
        "manifest",
    ]
    assert replayed[0][1] == artifact


@pytest.mark.parametrize("overflow", ["compressed", "expanded"])
def test_offline_delivery_rejects_oversized_spool_before_replay(monkeypatch, overflow):
    max_bytes = 16 * 1024 * 1024
    closed = []

    def chunks(path, timeout=None):
        try:
            if overflow == "compressed":
                # A gzip header then empty stored blocks: it inflates to nothing, so only
                # the download cap can stop it.
                yield b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"
                chunk = b"\x00\x00\x00\xff\xff" * 13107
                for _ in range(max_bytes // len(chunk) + 1):
                    yield chunk
            else:
                body = io.BytesIO()
                with tarfile.open(fileobj=body, mode="w:gz") as archive:
                    member = tarfile.TarInfo("outbound-spool/artifacts/large.bin")
                    member.size = max_bytes + 1
                    archive.addfile(member, io.BytesIO(b"\0" * member.size))
                yield body.getvalue()
        finally:
            closed.append(True)

    sandbox = _Sandbox()
    sandbox.fs.download_file_stream = chunks
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(get=lambda *args, **kwargs: sandbox)
    attempt = SimpleNamespace(
        provider_ref="sandbox-1", job=SimpleNamespace(max_artifact_bytes=0)
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_artifact",
        lambda *args, **kwargs: pytest.fail("oversized spool must not be replayed"),
    )

    with pytest.raises(HostedHarnessError) as error:
        gateway._recover_offline_delivery(attempt)
    assert error.value.code == "offline_delivery_too_large"
    assert closed == [True]


class _CountingFile:
    def __init__(self, file, read, written):
        self._file, self._read, self._written = file, read, written

    def read(self, *args):
        data = self._file.read(*args)
        self._read.append(len(data))
        return data

    def write(self, data):
        self._written.append(len(data))
        return self._file.write(data)

    def __getattr__(self, name):
        return getattr(self._file, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._file.close()


def _offline_recovery(monkeypatch, archive, *, max_artifact_bytes):
    """Gateway whose sandbox serves ``archive``, plus the bytes read from and written to its temp files."""
    read, written = [], []
    temporary_file = tempfile.TemporaryFile
    monkeypatch.setattr(
        tempfile,
        "TemporaryFile",
        lambda *args, **kwargs: _CountingFile(
            temporary_file(*args, **kwargs), read, written
        ),
    )
    sandbox = _Sandbox()
    sandbox.fs.download_file_stream = lambda path, timeout=None: iter([archive])
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = SimpleNamespace(get=lambda *args, **kwargs: sandbox)
    attempt = SimpleNamespace(
        id="attempt-1",
        provider_ref="sandbox-1",
        job=SimpleNamespace(max_artifact_bytes=max_artifact_bytes),
    )
    return gateway, attempt, read, written


def test_offline_delivery_reads_the_spool_once_in_any_archive_order(monkeypatch):
    # `tar -czf` stores members in directory order, but replay reads them sorted by name.
    # Store them in reverse so every replay step would seek backwards through a gzip stream.
    bodies = {}
    for _ in range(40):
        body = os.urandom(32 * 1024)
        bodies[hashlib.sha256(body).hexdigest()] = body
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for digest in sorted(bodies, reverse=True):
            metadata = json.dumps(
                {
                    "digest": digest,
                    "kind": "recording",
                    "size": len(bodies[digest]),
                    "content_type": "audio/wav",
                    "scenario_key": "one",
                }
            ).encode()
            for name, data in (
                (f"outbound-spool/artifacts/{digest}.json", metadata),
                (f"outbound-spool/artifacts/{digest}.bin", bodies[digest]),
            ):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        manifest = tarfile.TarInfo("outbound-spool/manifest.json")
        manifest.size = 2
        archive.addfile(manifest, io.BytesIO(b"{}"))
    gateway, attempt, read, _ = _offline_recovery(
        monkeypatch, gzip.compress(raw.getvalue()), max_artifact_bytes=16 * 1024 * 1024
    )
    replayed = {}
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_artifact",
        lambda *args, **kwargs: replayed.update(
            {kwargs["digest"]: kwargs["stream"].read()}
        ),
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_manifest",
        lambda *args, **kwargs: None,
    )

    assert gateway._recover_offline_delivery(attempt) is True
    assert replayed == bodies
    # Every artifact is read once from the inflated tar. Re-inflating the gzip stream on
    # each backward seek reads the spool roughly once per artifact instead.
    assert sum(len(body) for body in bodies.values()) <= sum(read)
    assert sum(read) <= 2 * len(raw.getvalue())


def test_offline_delivery_stops_inflating_a_spool_bomb_at_the_budget(monkeypatch):
    class Zeros(io.RawIOBase):
        def __init__(self, size):
            self.left = size

        def readinto(self, buffer):
            size = min(len(buffer), self.left)
            buffer[:size] = bytes(size)
            self.left -= size
            return size

    bomb = io.BytesIO()
    with tarfile.open(fileobj=bomb, mode="w:gz") as archive:
        member = tarfile.TarInfo("outbound-spool/artifacts/bomb.bin")
        # 16x the cap of a zero budget, about 256 KiB once gzipped.
        member.size = 256 * 1024 * 1024
        archive.addfile(member, Zeros(member.size))
    gateway, attempt, _, written = _offline_recovery(
        monkeypatch, bomb.getvalue(), max_artifact_bytes=0
    )
    max_bytes = 16 * 1024 * 1024

    tracemalloc.start()
    try:
        with pytest.raises(HostedHarnessError) as error:
            gateway._recover_offline_delivery(attempt)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert error.value.code == "offline_delivery_too_large"
    assert sum(written) <= max_bytes + 1024 * 1024
    assert peak < 8 * 1024 * 1024


def test_offline_delivery_rejects_a_sparse_member_larger_than_the_budget(monkeypatch):
    # A pax GNU-sparse header lets a 10 KiB tar claim a member larger than the budget, so
    # only the header-size cap, not the inflation cap, stops replay from reading it.
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("outbound-spool/events.spool.jsonl")
        member.pax_headers = {
            "GNU.sparse.map": "0,0",
            "GNU.sparse.numblocks": "1",
            "GNU.sparse.realsize": str(16 * 1024 * 1024 + 1),
        }
        archive.addfile(member, io.BytesIO(b""))
    gateway, attempt, _, _ = _offline_recovery(
        monkeypatch, gzip.compress(raw.getvalue()), max_artifact_bytes=0
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_event_batch",
        lambda *args, **kwargs: pytest.fail("a sparse member must not be replayed"),
    )

    with pytest.raises(HostedHarnessError) as error:
        gateway._recover_offline_delivery(attempt)
    assert error.value.code == "offline_delivery_too_large"


@pytest.mark.parametrize("damage", ["truncated", "trailing", "second_member"])
def test_offline_delivery_rejects_a_damaged_gzip_stream_before_replay(
    monkeypatch, damage
):
    metadata = {
        "digest": "one",
        "kind": "recording",
        "size": 1,
        "content_type": "audio/wav",
        "scenario_key": "one",
    }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, data in (
            ("outbound-spool/artifacts/one.json", json.dumps(metadata).encode()),
            ("outbound-spool/artifacts/one.bin", b"x"),
            ("outbound-spool/manifest.json", b"{}"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    tar = raw.getvalue()
    manifest_at = (
        tarfile.open(fileobj=io.BytesIO(tar))
        .getmember("outbound-spool/manifest.json")
        .offset
    )
    # Every case below inflates its first part to a tar that parses cleanly and holds a
    # replayable artifact, so only the stream check stands between it and a partial replay.
    if damage == "truncated":
        compressor = zlib.compressobj(wbits=zlib.MAX_WBITS | 16)
        body = compressor.compress(tar[:manifest_at]) + compressor.flush(
            zlib.Z_FULL_FLUSH
        )
        expected = EOFError
    elif damage == "trailing":
        body, expected = gzip.compress(tar) + b"\1", ValueError
    else:
        body = gzip.compress(tar[:manifest_at]) + gzip.compress(tar[manifest_at:])
        expected = ValueError
    gateway, attempt, _, _ = _offline_recovery(monkeypatch, body, max_artifact_bytes=0)
    monkeypatch.setattr(
        "simulate.services.hosted_harness_ingestion.ingest_artifact",
        lambda *args, **kwargs: pytest.fail("a damaged spool must not be replayed"),
    )

    with pytest.raises(expected):
        gateway._recover_offline_delivery(attempt)


def test_offline_control_processes_scenario_registration(monkeypatch):
    request_path = "/work/outbound-spool/control/one.request.json"
    response_path = "/work/outbound-spool/control/one.response.json"
    request = {
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "attempt_number": 1,
        "payload": {"operation": "provision", "personas": [{"scenario_key": "a"}]},
    }
    uploads = {}

    def download(path, timeout=None):
        if path == request_path:
            return json.dumps(request).encode()
        raise FileNotFoundError(path)

    sandbox = SimpleNamespace(
        process=SimpleNamespace(
            exec=lambda *args, **kwargs: SimpleNamespace(
                exit_code=0, result=request_path
            )
        ),
        fs=SimpleNamespace(
            download_file=download,
            upload_file=lambda body, path: uploads.__setitem__(path, body),
        ),
    )
    attempt = SimpleNamespace(id="attempt-1", job_id="job-1", attempt_number=1)
    monkeypatch.setattr(
        "simulate.services.hosted_harness.provision_scenarios",
        lambda actual_attempt, payload: {
            "result": {
                "run_test_id": "run-test-1",
                "scenarios": [{"scenario_key": "a", "scenario_id": "scenario-1"}],
            }
        },
    )

    HostedHarnessGateway._sync_offline_control(attempt, sandbox)

    assert json.loads(uploads[response_path]) == {
        "result": {
            "run_test_id": "run-test-1",
            "scenarios": [{"scenario_key": "a", "scenario_id": "scenario-1"}],
        }
    }


@pytest.mark.django_db
def test_daytona_launch_failure_enters_durable_retry_wait(organization):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="launch-timeout-retry"
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _FailingDaytonaCreate()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""

    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

    attempt.refresh_from_db()
    job.refresh_from_db()
    assert attempt.state == HostedHarnessAttempt.State.FAILED
    assert attempt.cleanup_verified_at is not None
    assert attempt.terminal_failure["code"] == "sandbox_launch_failed"
    assert attempt.terminal_failure["details"]["exception_type"] == "TimeoutError"
    assert job.state == HostedHarnessJob.State.RETRY_WAIT

    # The Temporal workflow polls the attempt returned by launch once to discover whether it
    # should relaunch.  A pre-create failure has no provider_ref, so reconciliation must return
    # the durable retry state without attempting a lookup for an empty sandbox id or overwriting
    # the original failure as ``sandbox_disappeared``.
    reconciled = gateway.reconcile_completed(attempt)
    attempt.refresh_from_db()
    assert reconciled.state == HostedHarnessJob.State.RETRY_WAIT
    assert attempt.terminal_failure["code"] == "sandbox_launch_failed"


@pytest.mark.django_db
def test_daytona_permanent_provider_rejection_fails_without_retry(organization):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="launch-provider-rejection"
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _ForbiddenDaytonaCreate()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""

    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

    attempt.refresh_from_db()
    job.refresh_from_db()
    assert attempt.state == HostedHarnessAttempt.State.FAILED
    assert attempt.cleanup_verified_at is not None
    assert attempt.terminal_failure["details"] == {
        "provider": "daytona",
        "exception_type": "RuntimeError",
        "provider_status_code": 403,
        "provider_reason": "organization_suspended_depleted_credits",
    }
    assert job.state == HostedHarnessJob.State.FAILED
    assert job.attempts.count() == 1


@pytest.mark.django_db
def test_daytona_launch_uploads_contract_files_and_starts_one_session(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="launch-gateway")
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200
    simulator_values = {
        "ALK_HARNESS": "vertex-gemini",
        "ALK_HARNESS_MODEL": "gemini-3.7-flash",
        "AGENTCC_BASE_URL": "https://gateway.example.test",
        "AGENTCC_API_KEY": "harness-virtual-key",
        "SIMULATOR_STT_PROVIDER": "deepgram",
    }
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: simulator_values,
    )

    selected_capacity = SandboxCapacity("selected", 6, 12_288, 20, 1)
    with patch(
        "simulate.services.harness_capacity.configured_capacity",
        return_value=selected_capacity,
    ):
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert attempt.state == HostedHarnessAttempt.State.RUNNING
    assert attempt.provider_ref == "sandbox-1"
    assert (
        client.params.cpu_units,
        client.params.memory_mb,
        client.params.disk_gb,
    ) == (
        6,
        12_288,
        20,
    )
    dispatched = json.loads(client.sandbox.fs.uploads["/work/job.json"])
    assert dispatched["runtime"]["cpu_units"] == client.params.cpu_units
    assert dispatched["runtime"]["memory_mb"] == client.params.memory_mb
    assert set(client.sandbox.fs.uploads) >= {
        "/work/source.tar.gz",
        "/work/job.json",
        "/run/futureagi/secrets.json",
        _SIMULATOR_SECRETS_PATH,
        "/run/futureagi/capabilities.json",
        "/run/futureagi/entrypoint-command-id",
    }
    assert "/run/futureagi/simulator-vertex-sa.json" not in client.sandbox.fs.uploads
    assert client.sandbox.process.sessions == ["alk-harness"]
    assert json.loads(client.sandbox.fs.uploads["/run/futureagi/secrets.json"]) == {}
    assert json.loads(client.sandbox.fs.uploads[_SIMULATOR_SECRETS_PATH]) == (
        simulator_values
    )
    assert b"harness-virtual-key" not in (
        client.sandbox.process.session_request.command.encode()
    )
    assert "--await-harness" in client.sandbox.process.session_request.command
    assert job.conversation.messages.get().content.startswith("Build the test environment")
    assert "; fi &&" in client.sandbox.process.session_request.command
    # Authoring and call execution are distinct bounded phases. The sandbox must survive the
    # former rather than using only the 10-minute call-runtime budget plus two minutes.
    assert client.params.ttl_seconds == 7200
    prepare_command = client.sandbox.process.exec_calls[0]
    assert not prepare_command.startswith("mkdir -p /work/authoring")
    assert (
        "if [ -f /work/authoring.tar.gz ]; then mkdir -p /work/authoring"
        in prepare_command
    )
    assert client.params.unrestricted_egress is False
    assert set(client.params.allowed_domains) == {
        "agent.example.com",
        "*.vapi.ai",
        "gateway.example.test",
        "ingest.example.com",
        "platform.example.com",
    }


@pytest.mark.django_db
def test_a_fresh_build_runs_the_staged_pipeline_when_the_builder_session_is_off(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="launch-gateway-staged")
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200
    settings.ALK_HOSTED_BUILDER_SESSION = False
    simulator_values = {
        "ALK_HARNESS": "vertex-gemini",
        "ALK_HARNESS_MODEL": "gemini-3.7-flash",
        "AGENTCC_BASE_URL": "https://gateway.example.test",
        "AGENTCC_API_KEY": "harness-virtual-key",
        "DEEPGRAM_API_KEY": "platform-simulator-deepgram",
    }
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: simulator_values,
    )

    selected_capacity = SandboxCapacity("selected", 6, 12_288, 20, 1)
    with patch(
        "simulate.services.harness_capacity.configured_capacity",
        return_value=selected_capacity,
    ):
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert attempt.state == HostedHarnessAttempt.State.RUNNING
    command = client.sandbox.process.session_request.command
    assert "--await-harness" not in command and "--adjustments" in command
    assert not job.conversation.messages.filter(content__startswith="Build the test environment").exists()



def _launch_and_read_job_json(
    organization, settings, *, requested_parallelism, provider_name="daytona"
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    payload["runtime"]["parallelism"] = requested_parallelism
    payload["scenario_count"] = requested_parallelism
    payload["runtime"].update(
        cpu_units=4 if provider_name == "e2b" else 8,
        memory_mb=8192,
        disk_gb=10,
    )
    job, _ = create_hosted_job(
        organization, payload, idempotency_key=f"parallelism-{requested_parallelism}"
    )
    client = _Daytona()
    if provider_name == "e2b":
        client.name = "e2b"
        client.runtime_name = settings.ALK_E2B_TEMPLATE_REFERENCE
        client.runtime_digest = settings.ALK_E2B_TEMPLATE_BUILD_ID
        client.max_egress_domains = None
    else:
        client.runtime_digest = "sha256:good"
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:good"
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200
    # Platform simulator config is deployment material; these tests are about the
    # admitted parallelism the guest receives, not what the sandbox is given.
    simulator_values = {
        "ALK_HARNESS": "vertex-gemini",
        "ALK_HARNESS_MODEL": "gemini-2.5-flash",
    }
    with patch(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        return_value=simulator_values,
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")

    job.refresh_from_db()
    dispatched = json.loads(client.sandbox.fs.uploads["/work/job.json"])
    return job, dispatched, client


@pytest.mark.django_db
def test_daytona_launch_clamps_guest_parallelism_when_disabled(organization, settings):
    # Flag off => W>1 is denied by the shared guard. The guest must be launched at
    # the ADMITTED W=1 even though the requested value stays on job.payload so a
    # later rerun re-evaluates against the then-current flag/digest.
    settings.HARNESS_PARALLELISM_ENABLED = False
    settings.HARNESS_PARALLEL_SNAPSHOT_DIGESTS = ["sha256:good"]
    settings.ALK_DAYTONA_DOCKERFILE = ""

    job, dispatched, _client = _launch_and_read_job_json(
        organization, settings, requested_parallelism=4
    )

    assert dispatched["runtime"]["parallelism"] == 1
    assert job.payload["runtime"]["parallelism"] == 4


@pytest.mark.django_db
def test_daytona_launch_passes_admitted_parallelism_when_enabled(
    organization, settings
):
    # Flag on and the registered digest is allowlisted => W>1 is admitted, so the
    # guest receives the requested W unchanged.
    settings.HARNESS_PARALLELISM_ENABLED = True
    settings.HARNESS_PARALLEL_SNAPSHOT_DIGESTS = ["sha256:good"]
    settings.ALK_DAYTONA_DOCKERFILE = ""

    job, dispatched, _client = _launch_and_read_job_json(
        organization, settings, requested_parallelism=4
    )

    assert dispatched["runtime"]["parallelism"] == 4
    assert job.payload["runtime"]["parallelism"] == 4


@pytest.mark.django_db
def test_e2b_standard_resources_reach_guest_admission(organization, settings):
    settings.HOSTED_SANDBOX_PROVIDER = "e2b"
    settings.HARNESS_PARALLELISM_ENABLED = True
    settings.HARNESS_RESOURCE_PROFILES = []
    settings.HARNESS_PARALLEL_SNAPSHOT_DIGESTS = ["build-123"]
    settings.ALK_E2B_TEMPLATE_REFERENCE = "alk-hosted-e2b:build-123"
    settings.ALK_E2B_TEMPLATE_BUILD_ID = "build-123"
    settings.ALK_E2B_TEMPLATE_CPU_UNITS = 4
    settings.ALK_E2B_TEMPLATE_MEMORY_MB = 8192
    settings.ALK_E2B_TEMPLATE_DISK_GB = 10

    job, dispatched, _client = _launch_and_read_job_json(
        organization, settings, requested_parallelism=2, provider_name="e2b"
    )

    assert job.payload["runtime"]["parallelism"] == 2
    assert dispatched["runtime"]["parallelism"] == 2


@pytest.mark.django_db
def test_unified_provider_import_authoring_receives_one_shot_target_key(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    payload["agent"] = {
        "connector": "retell",
        "mode": "provider_import",
        "config": {"agent_id": "source-retell-agent"},
        "secret_refs": {"RETELL_API_KEY": {}},
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="unified-provider-import-authoring"
    )
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = []
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        lambda _self, _job: {
            "RETELL_API_KEY": "retell-target-secret",
            "UNRELATED_AGENT_SECRET": "must-not-enter-authoring-file",
        },
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: {},
    )

    gateway.launch(job, endpoint_base_url="https://platform.example.com")

    one_shot_path = "/run/futureagi/authoring-target-secrets.json"
    assert json.loads(client.sandbox.fs.uploads[one_shot_path]) == {
        "RETELL_API_KEY": "retell-target-secret"
    }
    command = client.sandbox.process.session_request.command
    assert f"--target-secrets {one_shot_path}" in command
    assert "--provider-profile-cache /work/provider-import-profile.json" in command
    assert "retell-target-secret" not in command
    assert (
        "must-not-enter-authoring-file"
        not in client.sandbox.fs.uploads[one_shot_path].decode()
    )
    assert one_shot_path in client.sandbox.process.exec_calls[0]


@pytest.mark.django_db
def test_daytona_adjustment_is_persisted_and_delivered_to_active_authoring(
    organization, settings, tmp_path
):
    payload = _payload()
    payload["scenario_count"] = 2
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="adjust-gateway")
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    dockerfile = tmp_path / "Dockerfile.hosted"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    gateway.dockerfile = str(dockerfile)
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    job.refresh_from_db()
    attempt = HostedHarnessAttempt.no_workspace_objects.get(
        job=job, attempt_number=job.current_attempt_number
    )
    assert attempt.snapshot_name == "alk-hosted-v1"

    adjusted = gateway.adjust(
        job,
        {
            "instruction": "Create 1 more scenario covering discounts",
            "client_request_id": "browser-1",
            "scenario_delta": 1,
        },
    )

    assert adjusted.scenario_count == 3
    records = [
        json.loads(line)
        for line in client.sandbox.fs.uploads["/run/futureagi/adjustments.jsonl"]
        .decode()
        .splitlines()
    ]
    assert records[0]["target_stage"] == "scenarios"
    assert records[0]["scenario_delta"] == 1
    assert records[0]["status"] == "pending"
    assert adjusted.payload["metadata"]["adjustments"] == records
    assert adjusted.payload["scenario_count"] == 3


@pytest.mark.django_db
def test_adjustment_goes_to_the_stage_the_chat_names(organization, settings, tmp_path):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="adjust-named-stage")
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    dockerfile = tmp_path / "Dockerfile.hosted"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    gateway.dockerfile = str(dockerfile)
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    job.refresh_from_db()

    adjusted = gateway.adjust(
        job,
        {
            "instruction": "Identity needs a date of birth; update the scenario callers too",
            "client_request_id": "chat-1",
            "target_stage": "environment",
        },
    )

    assert adjusted.payload["metadata"]["adjustments"][-1]["target_stage"] == "environment"


@pytest.mark.django_db
def test_daytona_retry_replays_persisted_adjustments_before_authoring(
    organization, settings
):
    payload = _payload()
    payload["scenario_count"] = 2
    payload["metadata"] = {
        "adjustments": [
            {
                "adjustment_id": "adjustment-1",
                "instruction": "Create one more discount scenario",
                "target_stage": "scenarios",
                "scenario_delta": 1,
                "status": "applied",
            }
        ]
    }
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="retry-adjustment-replay"
    )
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]

    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")

    replayed = [
        json.loads(line)
        for line in client.sandbox.fs.uploads[_ADJUSTMENTS_PATH].decode().splitlines()
    ]
    assert replayed == payload["metadata"]["adjustments"]
    assert client.sandbox.process.session_request is not None


@pytest.mark.django_db
def test_daytona_adjustment_takes_the_scenario_count_it_is_given(
    organization, settings, tmp_path
):
    payload = _payload()
    payload["scenario_count"] = 2
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="adjust-word-number"
    )
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    dockerfile = tmp_path / "Dockerfile.hosted"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    gateway.dockerfile = str(dockerfile)
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    job.refresh_from_db()

    adjusted = gateway.adjust(
        job,
        {
            "instruction": "Cover discounts",
            "client_request_id": "browser-word-1",
            "scenario_delta": 1,
        },
    )

    assert adjusted.scenario_count == 3
    assert adjusted.payload["scenario_count"] == 3
    assert adjusted.payload["metadata"]["adjustments"][0]["scenario_delta"] == 1


@pytest.mark.django_db
def test_daytona_livekit_launch_uses_coturn_domain_allowlist(
    organization, settings, monkeypatch
):
    payload = _payload()
    payload["agent"]["connector"] = "livekit"
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="launch-livekit-webrtc"
    )
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = "sha256:" + "b" * 64
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = [
        "ingest.example.com",
        "coturn.turn-eu.futureagi.com",
    ]
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: {},
    )

    gateway.launch(job, endpoint_base_url="https://platform.example.com")

    assert client.params.allowed_cidrs == ()
    assert set(client.params.allowed_domains) == {
        "agent.example.com",
        "coturn.turn-eu.futureagi.com",
        "ingest.example.com",
        "platform.example.com",
    }


@pytest.mark.django_db
def test_gateway_polls_diagnostics_to_s3_and_finalizes_before_cleanup(
    organization, monkeypatch
):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="polled-diagnostics"
    )
    attempt = register_attempt(
        job.id,
        endpoint_base_url="https://platform.example.com",
        provider_ref="sandbox-1",
        snapshot_name="alk-hosted-v1",
    ).attempt
    client = _Daytona()
    process = client.sandbox.process
    process.entrypoint_output = (
        "\x01\x02\x1b[31mproduction prod true 13 target-secret "
        "custom-secret-value\x1b[0m\nwaiting"
    )
    process.process_output = "worker simulator-secret\nready"
    uploaded = []

    class _Storage:
        def put_object(self, **kwargs):
            client.lifecycle.append("upload")
            uploaded.append(kwargs["data"].read())

    monkeypatch.setattr(
        "simulate.services.hosted_harness_diagnostics.get_storage_client",
        lambda: _Storage(),
    )
    resolver_calls = []

    def _resolve_target_secrets(_resolver, _job):
        resolver_calls.append(_job.id)
        return {
            "TARGET_API_KEY": "target-secret",
            "SHORT_API_KEY": "prod",
            "FEATURE_FLAG": "true",
            "STRIPE_SK": "custom-secret-value",
        }

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        _resolve_target_secrets,
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.resolve_platform_simulator_secrets",
        lambda: {"SIMULATOR_API_KEY": "simulator-secret"},
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client

    assert gateway.reconcile_completed(attempt) is None
    attempt.refresh_from_db()
    running = json.loads(gzip.decompress(uploaded[-1]))
    assert running["final"] is False
    assert running["entrypoint_log"] == (
        "production prod true 13 [REDACTED] [REDACTED]\nwaiting"
    )
    assert running["process_logs"] == "worker [REDACTED]\nready"
    assert attempt.diagnostics_final is False
    assert attempt.diagnostics_object_key.endswith(f"/{attempt.id}.json.gz")
    assert client.deleted is False

    process.command_exit_code = 2
    process.command_status = "finished"
    process.entrypoint_output += "\nfatal"
    gateway.reconcile_completed(attempt)

    attempt.refresh_from_db()
    final = json.loads(gzip.decompress(uploaded[-1]))
    assert final["final"] is True
    assert final["exit_code"] == 2
    assert final["entrypoint_log"].endswith("fatal")
    assert attempt.diagnostics_final is True
    assert attempt.diagnostics_size == len(uploaded[-1])
    assert client.lifecycle[-2:] == ["upload", "delete"]
    assert resolver_calls == [job.id]
    assert serialize_job(job)["runtime"] == {
        "sandbox_id": "sandbox-1",
        "diagnostics": {
            "object_key": attempt.diagnostics_object_key,
            "sha256": attempt.diagnostics_sha256,
            "size": attempt.diagnostics_size,
            "captured_at": attempt.diagnostics_captured_at.isoformat(),
            "final": True,
            "error": "",
        },
    }


@pytest.mark.django_db
def test_cancel_signals_guest_before_provider_delete(organization, monkeypatch):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="cancel-gateway")
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")
    cleanup_order = []
    monkeypatch.setattr(
        "simulate.services.phone_telephony.cleanup_hosted_phone_rooms",
        lambda _: cleanup_order.append("livekit"),
    )

    def delete_and_record(_attempt, *, after_provider_cleanup=None):
        cleanup_order.append("sandbox")
        if after_provider_cleanup is not None:
            after_provider_cleanup()
        return job

    monkeypatch.setattr(gateway, "_delete_and_record", delete_and_record)

    gateway.cancel(job, reason="user_canceled")

    assert (
        b'"reason":"user_canceled"'
        in client.sandbox.fs.uploads["/run/futureagi/cancel.json"]
    )
    assert any(
        "pkill -TERM -f '[f]i.alk.harness.hosted_entrypoint'" in command
        for command in client.sandbox.process.exec_calls
    )
    attempt = HostedHarnessAttempt.no_workspace_objects.get(job=job)
    assert attempt.terminal_stage == "canceled"
    assert attempt.terminal_reason == "user_canceled"
    assert attempt.terminal_failure is None
    assert cleanup_order == ["livekit", "sandbox", "livekit"]


@pytest.mark.django_db
def test_cancel_deletes_when_guest_signal_fails(organization, monkeypatch):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="cancel-signal-failure"
    )
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")

    def fail_upload(_content, _path):
        raise RuntimeError("guest control channel unavailable")

    monkeypatch.setattr(client.sandbox.fs, "upload_file", fail_upload)

    canceled = gateway.cancel(job, reason="user_canceled")

    assert client.deleted is True
    assert canceled.state == HostedHarnessJob.State.CANCELED


@pytest.mark.django_db
def test_cancel_retries_room_cleanup_after_sandbox_is_already_deleted(
    organization, monkeypatch
):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="cancel-room-cleanup-retry"
    )
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        gateway.launch(job, endpoint_base_url="https://platform.example.com")

    cleanup_attempts = []

    # The pre-delete hangup is best effort; the verified pass after the sandbox
    # is gone is the one whose failure must be retried.
    def cleanup(_job):
        cleanup_attempts.append(str(_job.id))
        if len(cleanup_attempts) == 2:
            raise RuntimeError("LiveKit temporarily unavailable")

    monkeypatch.setattr(
        "simulate.services.phone_telephony.cleanup_hosted_phone_rooms", cleanup
    )

    with pytest.raises(RuntimeError, match="LiveKit temporarily unavailable"):
        gateway.cancel(job, reason="user_canceled")

    attempt = HostedHarnessAttempt.no_workspace_objects.get(job=job)
    assert client.deleted is True
    assert attempt.cleanup_verified_at is None

    canceled = gateway.cancel(job, reason="user_canceled")

    assert cleanup_attempts == [str(job.id)] * 4
    assert canceled.state == HostedHarnessJob.State.CANCELED
    attempt.refresh_from_db()
    assert attempt.cleanup_verified_at is not None


@pytest.mark.django_db
def test_cancel_immediately_projects_cleaning_up_stage(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="cancel-visible-stage"
    )

    canceled = request_cancellation(job, "user_canceled")

    assert canceled.state == HostedHarnessJob.State.CLEANING_UP
    assert canceled.current_stage == HostedHarnessJob.State.CLEANING_UP
    assert canceled.cancel_requested_at is not None


@pytest.mark.django_db
def test_reconcile_relaunches_infra_failure_until_budget_then_fails(
    organization, monkeypatch
):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="retry-gateway")
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""

    def _fake_delete(attempt, *, retry_pending=False):
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    acquire = patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    )

    # Attempt 1 crashes (exit 2): infrastructure failure, 1 < 2 -> RETRY_WAIT.
    with acquire:
        attempt1 = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    assert attempt1.attempt_number == 1
    monkeypatch.setattr(gateway, "inspect", lambda _: {"exit_code": 2, "logs": "boom"})
    assert gateway.reconcile_completed(attempt1).state == (
        HostedHarnessJob.State.RETRY_WAIT
    )

    # Relaunch a fresh attempt; register_attempt supersedes attempt 1.
    with acquire:
        attempt2 = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    assert attempt2.attempt_number == 2
    # Attempt 2 crashes again: budget spent (2 not < 2) -> terminal FAILED.
    assert gateway.reconcile_completed(attempt2).state == HostedHarnessJob.State.FAILED


@pytest.mark.django_db
@override_settings(ALK_HOSTED_PROVIDER_UNREACHABLE_GRACE_SECONDS=900)
def test_reconcile_tolerates_brief_daytona_toolbox_outage(organization, monkeypatch):

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="brief-toolbox-outage"
    )
    attempt = register_attempt(
        job.id,
        endpoint_base_url="https://platform.example.com",
        provider_ref="sandbox-1",
    ).attempt
    attempt.heartbeat_at = timezone.now() - timedelta(minutes=4)
    attempt.save(update_fields=["heartbeat_at", "updated_at"])
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: (_ for _ in ()).throw(
            SandboxProviderError("toolbox unavailable", status_code=502)
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_delete_and_record",
        lambda *args, **kwargs: pytest.fail("recent outage must not delete sandbox"),
    )

    assert gateway.reconcile_completed(attempt) is None
    attempt.refresh_from_db()
    assert attempt.state == HostedHarnessAttempt.State.REGISTERED
    assert attempt.terminal_failure is None


@pytest.mark.django_db
def test_reconcile_replaces_daytona_sandbox_after_toolbox_grace(
    organization, monkeypatch
):

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="stale-toolbox-outage"
    )
    attempt = register_attempt(
        job.id,
        endpoint_base_url="https://platform.example.com",
        provider_ref="sandbox-1",
    ).attempt
    attempt.heartbeat_at = timezone.now() - timedelta(minutes=4)
    attempt.save(update_fields=["heartbeat_at", "updated_at"])
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: (_ for _ in ()).throw(
            SandboxProviderError("toolbox unavailable", status_code=502)
        ),
    )
    captured = {}

    def _fake_delete(stale_attempt, *, retry_pending=False):
        captured["retry_pending"] = retry_pending
        return record_cleanup(
            stale_attempt.id,
            provider_ref=str(stale_attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)

    reconciled = gateway.reconcile_completed(attempt)

    attempt.refresh_from_db()
    assert attempt.state == HostedHarnessAttempt.State.FAILED
    assert attempt.terminal_failure["domain"] == "infrastructure"
    assert attempt.terminal_failure["code"] == "sandbox_unreachable"
    assert captured["retry_pending"] is True
    assert reconciled.state == HostedHarnessJob.State.RETRY_WAIT


@pytest.mark.django_db
def test_reconcile_waits_when_daytona_deletion_is_already_in_progress(
    organization, monkeypatch
):

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="toolbox-delete-in-progress"
    )
    attempt = register_attempt(
        job.id,
        endpoint_base_url="https://platform.example.com",
        provider_ref="sandbox-1",
    ).attempt
    attempt.heartbeat_at = timezone.now() - timedelta(minutes=4)
    attempt.save(update_fields=["heartbeat_at", "updated_at"])
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: (_ for _ in ()).throw(
            SandboxProviderError("toolbox unavailable", status_code=502)
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_delete_and_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            SandboxConflictError("sandbox state change in progress", status_code=409)
        ),
    )

    assert gateway.reconcile_completed(attempt) is None
    attempt.refresh_from_db()
    assert attempt.state == HostedHarnessAttempt.State.FAILED
    assert attempt.terminal_failure["code"] == "sandbox_unreachable"


@pytest.mark.django_db
@pytest.mark.parametrize("exit_code", [0, 3, 78])
def test_reconcile_terminal_and_authoring_exits_never_retry(
    organization, monkeypatch, exit_code
):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(organization, payload, idempotency_key="noretry-gateway")
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    captured = {}

    def _fake_delete(attempt, *, retry_pending=False):
        captured["retry_pending"] = retry_pending
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    acquire = patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    )

    with acquire:
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(
        gateway, "inspect", lambda _: {"exit_code": exit_code, "logs": ""}
    )
    assert gateway.reconcile_completed(attempt).state != (
        HostedHarnessJob.State.RETRY_WAIT
    )
    assert captured["retry_pending"] is False

    if exit_code == 78:
        attempt.refresh_from_db()
        assert attempt.terminal_failure["domain"] == "environment"
        assert attempt.terminal_failure["code"] == "authoring_runtime_validation_failed"


@pytest.mark.django_db
def test_reconcile_legacy_authoring_exit_is_not_reported_as_guest_crash(
    organization, monkeypatch
):
    from simulate.services.hosted_harness import record_cleanup

    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="authoring-exit1-gateway"
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    captured = {}

    def _fake_delete(attempt, *, retry_pending=False):
        captured["retry_pending"] = retry_pending
        return record_cleanup(
            attempt.id,
            provider_ref=str(attempt.provider_ref),
            verified_absent=True,
            retry_pending=retry_pending,
            details={"provider": "test"},
        )

    monkeypatch.setattr(gateway, "_delete_and_record", _fake_delete)
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        attempt = gateway.launch(job, endpoint_base_url="https://platform.example.com")
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: {
            "exit_code": 1,
            "logs": "No world was saved.\nautomatic run stopped: environment failed",
            "process_logs": "",
        },
    )

    gateway.reconcile_completed(attempt)

    attempt.refresh_from_db()
    assert captured["retry_pending"] is False
    assert attempt.terminal_failure["domain"] == "environment"
    assert attempt.terminal_failure["stage"] == "generating_environment"
    assert attempt.terminal_failure["code"] == "authoring_failed"


@pytest.mark.django_db
def test_reconcile_exit0_refreshes_terminal_delivery_flags(organization, monkeypatch):
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    job, _ = create_hosted_job(
        organization, payload, idempotency_key="terminal-refresh-gateway"
    )
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = _Daytona()
    gateway.snapshot = "alk-hosted-v1"
    gateway.snapshot_digest = ""
    with patch(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        return_value=(b"archive", ""),
    ):
        stale_attempt = gateway.launch(
            job, endpoint_base_url="https://platform.example.com"
        )

    # Model the ingestion requests committing after launch returned but before the provider poll
    # observed process exit.  `stale_attempt` deliberately still carries both False values.
    HostedHarnessAttempt.no_workspace_objects.filter(id=stale_attempt.id).update(
        terminal_event_received=True,
        manifest_acked=True,
        terminal_stage="completed",
        terminal_failure=None,
    )
    assert stale_attempt.terminal_event_received is False
    assert stale_attempt.manifest_acked is False

    captured = {}

    def fake_delete(attempt, *, retry_pending=False):
        captured["attempt"] = attempt
        return attempt.job

    monkeypatch.setattr(gateway, "_delete_and_record", fake_delete)
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: {"exit_code": 0, "logs": "", "process_logs": ""},
    )

    gateway.reconcile_completed(stale_attempt)

    refreshed = captured["attempt"]
    assert refreshed.terminal_event_received is True
    assert refreshed.manifest_acked is True
    assert refreshed.terminal_stage == "completed"
    assert refreshed.terminal_failure is None


@pytest.mark.django_db(transaction=True)
def test_fresh_lease_starts_chat_once_for_concurrent_messages(
    organization, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    from simulate.services.hosted_harness_conversation import ensure_conversation
    from simulate.services.hosted_harness_gateway import _CHAT_SESSION

    job, _ = create_hosted_job(organization, _payload(), idempotency_key="fresh-chat")
    attempt = register_attempt(
        job.id, endpoint_base_url="https://platform.example"
    ).attempt
    attempt.provider_ref = "sandbox-1"
    attempt.state = HostedHarnessAttempt.State.RUNNING
    attempt.save()
    job.refresh_from_db()
    conversation = ensure_conversation(job)
    client = _Daytona()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: {},
    )
    barrier = Barrier(2)

    def start():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return gateway.ensure_conversation_runtime(
                job,
                conversation=conversation,
                endpoint_base_url="https://platform.example",
            ).state
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start) for _ in range(2)]
        states = [future.result(timeout=30) for future in futures]
    # The loser of the start claim waits on it instead of launching a second process; the
    # lease stays starting until the launched process polls.
    assert states == ["starting", "starting"]
    assert client.sandbox.process.sessions == [_CHAT_SESSION]
    assert "hosted_chat_entrypoint" in client.sandbox.process.session_request.command
    assert client.deleted is False


@pytest.mark.django_db
def test_hosted_execution_cancel_signals_workflow_without_deleting_sandbox(
    organization, monkeypatch
):
    from simulate.models import RunTest, TestExecution
    from simulate.views.run_test import TestExecutionCancelView

    run = RunTest.objects.create(name="Cancel hosted run", organization=organization)
    execution = TestExecution.objects.create(run_test=run)
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="cancel-workflow"
    )
    job.test_execution = execution
    job.save(update_fields=["test_execution"])
    signaled = []
    monkeypatch.setattr(
        "simulate.temporal.client.cancel_hosted_harness_gateway_workflow",
        lambda job_id: signaled.append(job_id),
    )
    with patch.object(HostedHarnessGateway, "cancel") as delete_fallback:
        result = TestExecutionCancelView()._cancel_with_temporal(execution)
    assert result["success"] is True
    assert signaled == [str(job.id)]
    delete_fallback.assert_not_called()
    job.refresh_from_db()
    assert job.state == HostedHarnessJob.State.CLEANING_UP
    assert job.cancel_reason == "user_canceled"


_OBSERVE_NAMES = (
    "HARNESS_OBSERVABILITY",
    "FI_API_KEY",
    "FI_SECRET_KEY",
    "FI_HARNESS_PROJECT",
)


def _set_observe_env(
    monkeypatch, flag, *, api_key="observe-key", secret="observe-secret"
):
    for name in _OBSERVE_NAMES:
        monkeypatch.delenv(name, raising=False)
    if flag is not None:
        monkeypatch.setenv("HARNESS_OBSERVABILITY", flag)
    if api_key:
        monkeypatch.setenv("FI_API_KEY", api_key)
    if secret:
        monkeypatch.setenv("FI_SECRET_KEY", secret)
    monkeypatch.setenv("FI_HARNESS_PROJECT", "hosted-harness")


@pytest.mark.parametrize("flag", [None, "", "off", "0", "false", "maybe"])
def test_platform_simulator_material_keeps_the_harness_untraced_without_opt_in(
    monkeypatch, flag
):
    _set_observe_env(monkeypatch, flag)

    values = _platform_simulator_material()

    # The keys travel regardless, because the harness grades with them.
    assert values["FI_API_KEY"] == "observe-key"
    assert values["FI_SECRET_KEY"] == "observe-secret"
    assert values["FI_HARNESS_PROJECT"] == "hosted-harness"
    assert values["HARNESS_OBSERVABILITY"] == "off"


@pytest.mark.parametrize("flag", ["on", "ON ", "true", "1", " Yes "])
def test_platform_simulator_material_lets_the_harness_trace_on_opt_in(
    monkeypatch, flag
):
    _set_observe_env(monkeypatch, flag)

    values = _platform_simulator_material()

    assert values["FI_API_KEY"] == "observe-key"
    assert values["FI_SECRET_KEY"] == "observe-secret"
    assert values["FI_HARNESS_PROJECT"] == "hosted-harness"
    assert values["HARNESS_OBSERVABILITY"] == "on"


@pytest.mark.parametrize(
    "missing", [{"api_key": ""}, {"secret": ""}, {"api_key": "", "secret": ""}]
)
@pytest.mark.parametrize("flag", [None, "on"])
def test_platform_simulator_material_forwards_no_key_that_is_not_set(
    monkeypatch, flag, missing
):
    _set_observe_env(monkeypatch, flag, **missing)

    values = _platform_simulator_material()

    if "api_key" in missing:
        assert "FI_API_KEY" not in values
    else:
        assert values["FI_API_KEY"] == "observe-key"
    if "secret" in missing:
        assert "FI_SECRET_KEY" not in values
    else:
        assert values["FI_SECRET_KEY"] == "observe-secret"
    # The flag is always sent; without keys the harness has nothing to trace with anyway.
    assert values["HARNESS_OBSERVABILITY"] == ("on" if flag else "off")


def test_the_collector_host_follows_the_keys_whatever_the_harness_flag(monkeypatch):
    monkeypatch.setenv("ALK_HOSTED_FI_BASE_URL", "https://collector.example.test")
    payload = {
        "agent": {"connector": "auto"},
        "security": {"allowed_egress_domains": []},
    }

    # The platform evals call the collector, so keys without the opt-in still need its host.
    for flag in (None, "off", "on"):
        _set_observe_env(monkeypatch, flag)
        values = _platform_simulator_material()
        assert values["FI_BASE_URL"] == "https://collector.example.test"
        assert "collector.example.test" in _resolved_egress_domains(
            payload, {}, values, None
        )

    # Without keys no rule adds the host in this setup, so the keys are what put it there.
    for api_key, secret in (("", ""), ("observe-key", ""), ("", "observe-secret")):
        _set_observe_env(monkeypatch, "on", api_key=api_key, secret=secret)
        values = _platform_simulator_material()
        assert values["FI_BASE_URL"] == "https://collector.example.test"
        assert "collector.example.test" not in _resolved_egress_domains(
            payload, {}, values, None
        )


def test_platform_simulator_prefers_public_callback_for_hosted_observability(
    monkeypatch,
):
    monkeypatch.delenv("ALK_HOSTED_FI_BASE_URL", raising=False)
    monkeypatch.setenv("HARNESS_PUBLIC_BASE_URL", "https://platform.example.test")
    monkeypatch.setenv("FI_BASE_URL", "http://backend:80")

    values = _platform_simulator_material()

    assert values["FI_BASE_URL"] == "https://platform.example.test"


# ---------------------------------------------------------------------------
# Observability export decided at launch
# ---------------------------------------------------------------------------

_GATEWAY = "simulate.services.hosted_harness_gateway"
_EXPORT_SNAPSHOT = "alk-hosted-v1"
_COLLECTOR = "https://collector.example.com"
_EXPORT_API_KEY = "export-api-key-0001"
_EXPORT_SECRET_KEY = "export-secret-key-0001"
_LEAK_SENTINEL = "LEAK-SENTINEL-DO-NOT-LOG"
_INGEST_API_ALIAS = "FI_SIMULATION_INGEST_API_KEY"
_INGEST_SECRET_ALIAS = "FI_SIMULATION_INGEST_SECRET_KEY"
_INGEST_PAIR = {
    _INGEST_API_ALIAS: _EXPORT_API_KEY,
    _INGEST_SECRET_ALIAS: _EXPORT_SECRET_KEY,
}


class _ExportCredentials(NamedTuple):
    api_key: str
    secret_key: str
    enabled: bool


class _CredentialsThatFailAfterTheKeys:
    """Hands out both key values, then fails when asked whether the key is enabled."""

    api_key = _EXPORT_API_KEY
    secret_key = _EXPORT_SECRET_KEY

    @property
    def enabled(self):
        raise RuntimeError(_LEAK_SENTINEL)


class _Logs:
    """Stands in for the module's structlog logger and keeps what it was told."""

    def __init__(self):
        self.records = []

    def __getattr__(self, level):
        def log(event, *args, **kwargs):
            self.records.append((level, event, kwargs))

        return log

    def events(self, name):
        return [(level, kw) for level, event, kw in self.records if event == name]

    def text(self):
        return repr(self.records)


class _OrderedDaytona(_Daytona):
    """A sandbox client that writes sandbox creation and every upload into one list."""

    def __init__(self, events):
        super().__init__()
        self.events = events
        stored = self.sandbox.fs.upload_file

        def upload(content, path):
            events.append(("upload", path))
            stored(content, path)

        self.sandbox.fs.upload_file = upload

    def create(self, params, **kwargs):
        self.events.append(("create", None))
        return super().create(params, **kwargs)


class _StopAfterJobJson(Exception):
    pass


class _StoppingFilesystem(_Filesystem):
    """Keeps the uploads, then ends the call once the job document is in."""

    def upload_file(self, content, path):
        super().upload_file(content, path)
        if path == "/work/job.json":
            raise _StopAfterJobJson


def _mark_text(mark):
    return mark.get("OTEL_RESOURCE_ATTRIBUTES", "")


class _ExportWorld:
    """A launchable job and every collaborator the export decision reads, at its seam.

    The database holds the agent, its observability row, the observability project and the
    job. The three producers owned by other tickets (the agent summary, the hidden
    simulation project and the key resolver) are stand-ins that record how they were
    called; the hidden project's row is made in the database when the stand-in is called.
    With ``hidden="real"`` the hidden project is made by the real function instead.
    """

    def __init__(
        self,
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        *,
        tag="a",
        bind="metadata",
        instrumentation="opentelemetry",
        kind="source",
        declared=None,
        provider="enabled",
        project="live",
        snapshot_setting=_EXPORT_SNAPSHOT,
        collector=_COLLECTOR,
        credentials="ok",
        summary_raises=False,
        hidden="ok",
        cap_at_baseline=False,
        base_domains=("ingest.example.com",),
        customer_domains=("agent.example.com",),
        stale_mark=None,
        library_missing=(),
    ):
        from model_hub.models.ai_model import AIModel
        from simulate.models import AgentDefinition, RunTest
        from tracer.models.observability_provider import (
            ObservabilityProvider,
            ProviderChoices,
        )
        from tracer.models.project import Project

        self.organization = organization
        self.workspace = workspace
        self.declared = dict(declared or {})
        self.events = []
        self.hidden = None
        self.hidden_calls = []
        self.summary_calls = []
        self.credential_calls = []
        self.logs = _Logs()
        self.collector = collector

        project_workspace = None if project == "no_workspace" else workspace
        project_organization = organization
        if project == "other_org":
            from accounts.models.organization import Organization

            project_organization = Organization.objects.create(name="Project owner org")
        self.project = Project.no_workspace_objects.create(
            name=f"Support Bot {tag}",
            organization=project_organization,
            workspace=project_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        if project == "no_workspace":
            # A save hook gives every project a workspace, so a legacy row without one can only be
            # made by an update that skips the hook.
            Project.no_workspace_objects.filter(id=self.project.id).update(
                workspace=None
            )
            self.project.refresh_from_db()
        if project == "deleted":
            self.project.deleted = True
            self.project.save()
        self.provider = None
        if provider != "missing":
            self.provider = ObservabilityProvider.no_workspace_objects.create(
                project=self.project,
                provider=ProviderChoices.OTHERS,
                organization=organization,
                workspace=workspace,
                enabled=provider in {"enabled", "deleted"},
            )
            if provider == "deleted":
                self.provider.deleted = True
                self.provider.save()
        self.agent = AgentDefinition.no_workspace_objects.create(
            agent_name=f"Code agent {tag}",
            agent_type=AgentDefinition.AgentTypeChoices.TEXT,
            inbound=True,
            description="A code agent",
            organization=organization,
            workspace=workspace,
            languages=["en"],
            observability_provider=self.provider,
        )

        payload = _payload()
        payload["source"] = {
            "kind": "remote",
            "endpoint": "https://agent.example.com",
            "visibility": "public",
        }
        payload["security"]["allowed_egress_domains"] = list(customer_domains)
        payload["agent"]["secret_refs"] = {
            alias: {
                "manager": "platform-vault",
                "key": f"vault-{alias}",
                "purpose": "target_provider",
            }
            for alias in self.declared
        }
        if stale_mark is not None:
            payload["metadata"]["customer_code_environment"] = dict(stale_mark)
        bound_id = {
            "metadata": str(self.agent.id),
            "bad_id": "not-a-uuid",
            "other_org": None,
            "deleted": None,
        }.get(bind)
        if bind == "other_org":
            from accounts.models.organization import Organization

            stranger = AgentDefinition.no_workspace_objects.create(
                agent_name="Other tenant",
                agent_type=AgentDefinition.AgentTypeChoices.TEXT,
                inbound=True,
                description="x",
                organization=Organization.objects.create(name="Other tenant org"),
                languages=["en"],
            )
            bound_id = str(stranger.id)
        if bind == "deleted":
            self.agent.deleted = True
            self.agent.save()
            bound_id = str(self.agent.id)
        if bound_id:
            payload["metadata"]["agent_definition_id"] = bound_id
        self.job, _ = create_hosted_job(
            organization,
            payload,
            idempotency_key=f"export-{tag}-{uuid.uuid4().hex[:8]}",
            workspace=workspace,
        )
        if bind == "run_test":
            run_test = RunTest.objects.create(
                name=f"Bound run {tag}",
                organization=organization,
                agent_definition=self.agent,
            )
            self.job.run_test = run_test
            self.job.save(update_fields=["run_test"])

        self.client = _OrderedDaytona(self.events)
        self.gateway = object.__new__(HostedHarnessGateway)
        self.gateway.client = self.client
        self.gateway.snapshot = _EXPORT_SNAPSHOT
        self.gateway.snapshot_digest = "sha256:good"
        self.client.runtime_digest = "sha256:good"
        settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = list(base_domains)
        settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
        settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200

        for name in (
            "ALK_HOSTED_FI_BASE_URL",
            "HARNESS_PUBLIC_BASE_URL",
            "FI_BASE_URL",
        ):
            monkeypatch.delenv(name, raising=False)
        if collector is not None:
            monkeypatch.setenv("ALK_HOSTED_FI_BASE_URL", collector)
        if snapshot_setting:
            monkeypatch.setenv(
                "ALK_HOSTED_OBSERVABILITY_EXPORT_SNAPSHOTS", snapshot_setting
            )
        else:
            monkeypatch.delenv(
                "ALK_HOSTED_OBSERVABILITY_EXPORT_SNAPSHOTS", raising=False
            )
        monkeypatch.setattr(f"{_GATEWAY}.structlog_logger", self.logs)
        monkeypatch.setattr(
            f"{_GATEWAY}.PlatformSecretResolver.resolve",
            lambda _self, _job: dict(self.declared),
        )

        summary_module = ModuleType("simulate.services.agent_setup")

        def agent_summary(agent_definition_id, *, agent_version_id=None):
            self.summary_calls.append(agent_definition_id)
            if summary_raises:
                raise RuntimeError(_LEAK_SENTINEL)
            return SimpleNamespace(kind=kind, instrumentation=instrumentation)

        summary_module.agent_summary = agent_summary
        monkeypatch.setitem(
            sys.modules, "simulate.services.agent_setup", summary_module
        )

        ingest_module = ModuleType("tracer.services.collector_ingest")
        ingest_module.ExportCredentials = _ExportCredentials

        # Keyword-only, like the real one, so a positional call fails here too.
        def get_or_create_export_credentials(
            *, organization_id, workspace_id, agent_definition_id
        ):
            self.events.append(("key", None))
            self.credential_calls.append(
                (organization_id, workspace_id, agent_definition_id)
            )
            if credentials == "raises":
                raise RuntimeError(_LEAK_SENTINEL)
            if credentials == "none":
                return None
            if credentials == "fails_after_the_keys":
                return _CredentialsThatFailAfterTheKeys()
            if credentials == "no_api_key":
                return _ExportCredentials("", _EXPORT_SECRET_KEY, True)
            if credentials == "no_secret_key":
                return _ExportCredentials(_EXPORT_API_KEY, "", True)
            return _ExportCredentials(
                _EXPORT_API_KEY, _EXPORT_SECRET_KEY, credentials != "disabled"
            )

        ingest_module.get_or_create_export_credentials = (
            get_or_create_export_credentials
        )
        monkeypatch.setitem(
            sys.modules, "tracer.services.collector_ingest", ingest_module
        )

        if hidden != "real":
            from tracer.services.simulation_project import SimulationProjectError

            def ensure_simulation_project(agent):
                self.events.append(("hidden", None))
                self.hidden_calls.append(agent.id)
                if hidden == "raises":
                    raise SimulationProjectError(_LEAK_SENTINEL)
                hidden_workspace = agent.workspace
                if hidden == "other_workspace":
                    from accounts.models.workspace import Workspace

                    hidden_workspace = Workspace.objects.create(
                        name="Simulation workspace",
                        organization=agent.organization,
                        is_default=False,
                        is_active=True,
                        created_by=user,
                    )
                row = Project.with_simulation.create(
                    name=f"fi-simulation-{agent.id}",
                    organization=agent.organization,
                    workspace=hidden_workspace,
                    model_type=AIModel.ModelTypes.GENERATIVE_LLM,
                    trace_type="observe",
                    simulation_agent=agent,
                )
                if hidden == "no_workspace":
                    Project.with_simulation.filter(id=row.id).update(workspace=None)
                    row = Project.with_simulation.get(id=row.id)
                self.hidden = row
                return row

            # Only the function is replaced: other modules import names from this one.
            monkeypatch.setattr(
                "tracer.services.simulation_project.ensure_simulation_project",
                ensure_simulation_project,
            )
        for module in library_missing:
            monkeypatch.setitem(sys.modules, module, None)

        self.simulator_values = {
            "ALK_HARNESS": "vertex-gemini",
            "ALK_HARNESS_MODEL": "gemini-3.7-flash",
            "AGENTCC_BASE_URL": "https://gateway.example.test",
            "AGENTCC_API_KEY": "harness-virtual-key",
        }
        monkeypatch.setattr(
            f"{_GATEWAY}._platform_simulator_material",
            lambda: dict(self.simulator_values),
        )
        if cap_at_baseline:
            self.client.max_egress_domains = len(self.baseline_domains())

    def baseline_domains(self):
        return _resolved_egress_domains(
            self.job.payload,
            dict(self.declared),
            self.simulator_values,
            "platform.example.com",
        )

    def launch(self):
        self.attempt = self.gateway.launch(
            self.job, endpoint_base_url="https://platform.example.com"
        )
        return self

    @property
    def uploads(self):
        return self.client.sandbox.fs.uploads

    @property
    def job_json(self):
        return json.loads(self.uploads["/work/job.json"])

    @property
    def mark(self):
        return self.job_json["metadata"]["customer_code_environment"]

    @property
    def secrets(self):
        return json.loads(self.uploads["/run/futureagi/secrets.json"])

    @property
    def refs(self):
        return self.job_json["agent"]["secret_refs"]

    @property
    def domains(self):
        return set(self.client.params.allowed_domains)

    def export_log(self):
        return self.logs.events("observability_export_unavailable")


def _assert_launched_without_export(world, declared_secrets=None):
    """The run was made, marked, and given nothing of the export."""
    assert world.attempt.state == HostedHarnessAttempt.State.RUNNING
    mark = world.mark
    assert set(mark) == {"OTEL_RESOURCE_ATTRIBUTES"}
    assert "fi.simulation.origin=simulation" in _mark_text(mark)
    assert "project_name" not in _mark_text(mark)
    assert world.secrets == (declared_secrets or {})
    assert not any(
        ref.get("purpose") == "observability_export" for ref in world.refs.values()
    )
    assert "collector.example.com" not in world.domains
    assert _EXPORT_API_KEY.encode() not in world.uploads["/work/job.json"]
    assert _LEAK_SENTINEL not in world.logs.text()


@pytest.mark.django_db
def test_all_three_job_uploads_carry_the_mark_and_the_row_does_not(
    organization, workspace, user, settings, monkeypatch
):
    from tracer.services.simulation_mark import customer_code_environment

    stale = {"OTEL_STALE_VALUE": "from the row"}
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        bind="run_test",
        stale_mark=stale,
    ).launch()
    job = world.job
    expected = customer_code_environment(
        organization_id=str(job.organization_id),
        job_id=str(job.id),
        run_id=str(job.run_id),
        run_test_id=str(job.run_test_id),
        test_execution_id=None,
        project_name=world.hidden.name,
        collector_url=_COLLECTOR,
        export="our_keys",
        instrumentation="opentelemetry",
    )
    # The launch's document carries exactly the function's dictionary, in place of the
    # value the stored request had.
    assert world.mark == expected
    assert world.mark != stale
    # The object the launch was handed first, then the stored row.
    for read_back in (False, True):
        if read_back:
            job.refresh_from_db()
        assert job.payload["metadata"]["customer_code_environment"] == stale
        assert "fi.simulation" not in json.dumps(job.payload)

    # Authoring and chat are never bound: the mark, and no export, no log line.
    for upload_document in (_authoring_document, _chat_document):
        logs = _Logs()
        monkeypatch.setattr(f"{_GATEWAY}.structlog_logger", logs)
        document, source_job = upload_document(monkeypatch)
        mark = document["metadata"]["customer_code_environment"]
        assert mark == customer_code_environment(
            organization_id=str(source_job.organization_id),
            job_id=str(source_job.id),
            run_id=str(source_job.run_id),
            run_test_id=None,
            test_execution_id=None,
        )
        assert set(mark) == {"OTEL_RESOURCE_ATTRIBUTES"}
        assert "project_name" not in mark["OTEL_RESOURCE_ATTRIBUTES"]
        assert "fi.simulation.origin=simulation" in mark["OTEL_RESOURCE_ATTRIBUTES"]
        assert logs.events("observability_export_unavailable") == []
        assert logs.events("simulation_mark_unavailable") == []
        assert source_job.payload["metadata"]["customer_code_environment"] == stale


def _unbound_job():
    payload = _payload()
    payload["source"] = {
        "kind": "remote",
        "endpoint": "https://agent.example.com",
        "visibility": "public",
    }
    payload["metadata"] = {
        "customer_code_environment": {"OTEL_STALE_VALUE": "from the row"}
    }
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        run_test_id=None,
        test_execution_id=None,
        payload=payload,
        # A job with no environment of its own stands in for one.
        environment=None,
    )


def _authoring_document(monkeypatch, events=None):
    job = _unbound_job()
    client = _Daytona() if events is None else _OrderedDaytona(events)
    client.sandbox.fs = _StoppingFilesystem()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    monkeypatch.setattr(f"{_GATEWAY}._mark_stage", lambda *_: None)
    monkeypatch.setattr(
        f"{_GATEWAY}.HostedSourceAcquirer.acquire", lambda *_: (b"source", None)
    )
    monkeypatch.setattr(
        f"{_GATEWAY}._provider_import_authoring_material", lambda *_: ({}, "")
    )
    monkeypatch.setattr(f"{_GATEWAY}._platform_simulator_material", lambda: {})
    with pytest.raises(_StopAfterJobJson):
        gateway.author(job)
    return json.loads(client.sandbox.fs.uploads["/work/job.json"]), job


def _chat_document(monkeypatch, events=None):
    job = _unbound_job()
    client = _Daytona() if events is None else _OrderedDaytona(events)
    client.sandbox.fs = _StoppingFilesystem()
    gateway = object.__new__(HostedHarnessGateway)
    gateway.client = client
    monkeypatch.setattr(f"{_GATEWAY}.load_workspace_archive", lambda _c: None)
    monkeypatch.setattr(f"{_GATEWAY}._authoring_archive_for", lambda _j: None)
    monkeypatch.setattr(
        f"{_GATEWAY}.HostedSourceAcquirer.acquire", lambda *_: (b"source", None)
    )
    monkeypatch.setattr(
        f"{_GATEWAY}.issue_conversation_capability",
        lambda *_a, **_k: SimpleNamespace(document={"conversation": "x"}),
    )
    monkeypatch.setattr(f"{_GATEWAY}._platform_simulator_material", lambda: {})
    with pytest.raises(_StopAfterJobJson):
        gateway._start_chat_sandbox(
            job,
            conversation=SimpleNamespace(id=uuid.uuid4(), job_id=job.id),
            endpoint_base_url="https://platform.example.com",
        )
    return json.loads(client.sandbox.fs.uploads["/work/job.json"]), job


def test_authoring_and_chat_documents_carry_the_mark_even_when_the_function_fails(
    monkeypatch,
):
    # The upload of these two is the only place the mark can be lost, so a failing
    # function must give them an empty dictionary rather than stop the call.
    def explode(**_kwargs):
        raise RuntimeError(_LEAK_SENTINEL)

    monkeypatch.setattr(
        "tracer.services.simulation_mark.customer_code_environment", explode
    )
    for upload_document in (_authoring_document, _chat_document):
        logs = _Logs()
        monkeypatch.setattr(f"{_GATEWAY}.structlog_logger", logs)
        document, job = upload_document(monkeypatch)
        assert document["metadata"]["customer_code_environment"] == {}
        assert logs.events("simulation_mark_unavailable") == [
            ("warning", {"job_id": str(job.id), "reason": "raised"})
        ]
        assert _LEAK_SENTINEL not in logs.text()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "tag, bind, word",
    [
        ("otel", "metadata", "opentelemetry"),
        ("traceai", "run_test", "traceai"),
    ],
)
def test_export_environment_and_keys_for_a_bound_source_agent_with_observability_on(
    tag, bind, word, organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag=tag,
        bind=bind,
        instrumentation=word,
    ).launch()
    mark = world.mark
    attributes = _mark_text(mark)
    # The hidden project's name, never the observability project's.
    assert f"project_name={quote(world.hidden.name, safe='')}" in attributes
    assert quote(world.project.name, safe="") not in attributes
    assert world.hidden.name != world.project.name
    assert "fi.simulation.origin=simulation" in attributes
    assert "OTEL_EXPORTER_OTLP_PROTOCOL" not in mark
    assert world.export_log() == []
    # The document and the stored request: the refs and the dictionary are on the
    # sandbox's copy only.
    # Checked on the object the launch was handed, then on the stored row: a shared
    # nested dictionary would otherwise be written to without ever being saved.
    for read_back in (False, True):
        if read_back:
            world.job.refresh_from_db()
        assert "customer_code_environment" not in world.job.payload["metadata"]
        stored_refs = world.job.payload["agent"]["secret_refs"]
        assert not any(
            r["purpose"] == "observability_export" for r in stored_refs.values()
        )
        assert stored_refs == {}
    assert world.domains >= {"collector.example.com"}
    assert world.hidden_calls == [world.agent.id]
    assert world.summary_calls == [world.agent.id]
    # The key is the agent's, found by the hidden project's workspace.
    assert world.credential_calls == [
        (str(organization.id), str(world.hidden.workspace_id), str(world.agent.id))
    ]
    assert mark["FI_SIMULATION_EXPORT"] == "our_keys"
    assert mark["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == f"{_COLLECTOR}/v1/traces"
    expected_names = {
        "OTEL_RESOURCE_ATTRIBUTES",
        "FI_SIMULATION_EXPORT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    }
    if word == "traceai":
        assert mark["FI_BASE_URL"] == _COLLECTOR
        expected_names.add("FI_BASE_URL")
    assert set(mark) == expected_names
    # The same two secrets for both words, and none of the names an SDK reads.
    assert world.secrets == _INGEST_PAIR
    assert {
        alias: ref
        for alias, ref in world.refs.items()
        if ref["purpose"] == "observability_export"
    } == {
        alias: {
            "manager": "platform-config",
            "key": alias,
            "purpose": "observability_export",
        }
        for alias in _INGEST_PAIR
    }
    assert set(world.refs) == set(_INGEST_PAIR)
    # A key value never rides in the document, a log line, or an exception message.
    document = world.uploads["/work/job.json"]
    assert _EXPORT_API_KEY.encode() not in document
    assert _EXPORT_SECRET_KEY.encode() not in document
    assert _EXPORT_API_KEY not in world.logs.text()
    # The launch-time redaction set holds each value.
    redacted = cached_attempt_redaction_values(world.attempt.id)
    assert {_EXPORT_API_KEY, _EXPORT_SECRET_KEY} <= set(redacted)


@pytest.mark.django_db
@pytest.mark.parametrize("flag", [None, "off", "on"])
def test_the_harness_flag_never_changes_the_agents_own_export(
    flag, organization, workspace, user, settings, monkeypatch
):
    from tracer.services.simulation_mark import customer_code_environment

    backend_key = "backend-observe-key-0001"
    _set_observe_env(monkeypatch, flag, api_key=backend_key)
    # Same world as the bound-source export test above, except that the platform simulator
    # material is the real one for the four harness names, so the flag can reach the launch.
    world = _ExportWorld(organization, workspace, user, settings, monkeypatch)
    real_values = _platform_simulator_material()
    world.simulator_values.update(
        {name: real_values[name] for name in _OBSERVE_NAMES if name in real_values}
    )
    world.launch()

    # The export is the one the flag-free test above expects: the hidden project, the
    # collector, the two ingest aliases with the export's own values.
    job = world.job
    assert world.export_log() == []
    assert world.mark == customer_code_environment(
        organization_id=str(job.organization_id),
        job_id=str(job.id),
        run_id=str(job.run_id),
        run_test_id=None,
        test_execution_id=None,
        project_name=world.hidden.name,
        collector_url=_COLLECTOR,
        export="our_keys",
        instrumentation="opentelemetry",
    )
    assert world.secrets == _INGEST_PAIR
    assert set(world.refs) == set(_INGEST_PAIR)
    assert "collector.example.com" in world.domains
    assert world.credential_calls == [
        (str(organization.id), str(world.hidden.workspace_id), str(world.agent.id))
    ]
    # The backend's key is never the agent's: not among the plan's secrets, the document or
    # the agent's secrets file.
    assert backend_key not in world.secrets.values()
    assert backend_key.encode() not in world.uploads["/work/job.json"]
    assert backend_key.encode() not in world.uploads["/run/futureagi/secrets.json"]
    # The harness's own file always holds the keys (it grades with them); the flag decides only
    # whether it traces itself.
    harness_file = json.loads(world.uploads[_SIMULATOR_SECRETS_PATH])
    assert harness_file["FI_API_KEY"] == backend_key
    assert harness_file["FI_SECRET_KEY"] == "observe-secret"
    assert harness_file["HARNESS_OBSERVABILITY"] == ("on" if flag == "on" else "off")


@pytest.mark.django_db
def test_the_observability_project_is_never_written_and_only_the_hidden_one_is_made(
    organization, workspace, user, settings, monkeypatch
):
    from tracer.models.project import Project

    world = _ExportWorld(
        organization, workspace, user, settings, monkeypatch, tag="untouched"
    )
    before = Project.no_workspace_objects.get(id=world.project.id)
    projects_before = set(Project.with_simulation.values_list("id", flat=True))
    world.launch()
    assert world.export_log() == []
    after = Project.no_workspace_objects.get(id=world.project.id)
    assert (after.name, after.updated_at) == (before.name, before.updated_at)
    projects_after = set(Project.with_simulation.values_list("id", flat=True))
    assert projects_after - projects_before == {world.hidden.id}
    assert projects_before <= projects_after
    # The hidden project is left out of the managers the rest of the application reads.
    assert world.hidden.id not in set(
        Project.no_workspace_objects.values_list("id", flat=True)
    )


@pytest.mark.django_db
def test_an_observability_project_without_a_workspace_still_exports_to_the_hidden_one(
    organization, workspace, user, settings, monkeypatch
):
    # The row only gates the export; only the hidden project's workspace is read.
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="legacy",
        project="no_workspace",
    ).launch()
    assert world.project.workspace_id is None
    assert world.export_log() == []
    assert world.mark["FI_SIMULATION_EXPORT"] == "our_keys"
    assert world.secrets == _INGEST_PAIR


@pytest.mark.django_db
def test_the_key_is_asked_for_by_the_hidden_projects_workspace_when_it_is_another_one(
    organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="elsewhere",
        hidden="other_workspace",
    ).launch()
    assert world.hidden.workspace_id is not None
    assert world.hidden.workspace_id != world.project.workspace_id
    assert world.hidden.workspace_id != world.agent.workspace_id
    assert world.credential_calls == [
        (str(organization.id), str(world.hidden.workspace_id), str(world.agent.id))
    ]
    assert world.export_log() == []
    assert world.secrets == _INGEST_PAIR


@pytest.mark.django_db
def test_the_launch_through_the_real_hidden_project_function(
    organization, workspace, user, settings, monkeypatch
):
    from tracer.models.project import Project
    from tracer.services.simulation_project import simulation_project_name

    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="real",
        hidden="real",
    ).launch()
    assert world.export_log() == []
    name = simulation_project_name(world.agent.id)
    assert f"project_name={quote(name, safe='')}" in _mark_text(world.mark)
    hidden = Project.with_simulation.get(simulation_agent=world.agent)
    assert hidden.name == name
    assert hidden.workspace_id == workspace.id
    assert world.credential_calls == [
        (str(organization.id), str(hidden.workspace_id), str(world.agent.id))
    ]
    # The other managers leave the hidden project out, so a read through one of them
    # cannot be how the launch found it.
    assert hidden.id not in set(
        Project.no_workspace_objects.values_list("id", flat=True)
    )

    # Another job for the same agent finds the project and makes no second one.
    payload = json.loads(json.dumps(world.job.payload))
    payload.pop("run_id")
    world.job, _ = create_hosted_job(
        organization,
        payload,
        idempotency_key=f"export-real-{uuid.uuid4().hex[:8]}",
        workspace=workspace,
    )
    world.launch()
    assert world.export_log() == []
    assert f"project_name={quote(name, safe='')}" in _mark_text(world.mark)
    assert Project.with_simulation.filter(simulation_agent=world.agent).count() == 1


@pytest.mark.django_db
def test_a_deleted_hidden_project_warns_and_the_launch_goes_on_without_the_export(
    organization, workspace, user, settings, monkeypatch
):
    from tracer.models.project import Project
    from tracer.services.simulation_project import ensure_simulation_project

    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="gone",
        hidden="real",
    )
    hidden = ensure_simulation_project(world.agent)
    Project.with_simulation.filter(id=hidden.id).update(deleted=True)
    world.launch()
    assert world.export_log() == [
        (
            "warning",
            {"job_id": str(world.job.id), "reason": "SimulationProjectError"},
        )
    ]
    assert world.credential_calls == []
    _assert_launched_without_export(world)


@pytest.mark.django_db
def test_the_hidden_project_is_made_before_the_key_and_both_before_the_sandbox_exists(
    organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization, workspace, user, settings, monkeypatch, tag="order"
    ).launch()
    names = [name for name, _ in world.events]
    assert names.count("hidden") == 1
    assert names.count("key") == 1
    # The key is asked for the hidden project's workspace, so the project comes first.
    assert names.index("hidden") < names.index("key")
    # Before the sandbox is created, so before the first upload (and the attempt row).
    assert names.index("key") < names.index("create")
    assert names.index("key") < names.index("upload")


@pytest.mark.django_db
def test_a_trailing_slash_on_the_collector_never_doubles_the_traces_path(
    organization, workspace, user, settings, monkeypatch
):
    for tag, slashes in (("slash1", "/"), ("slash2", "//")):
        world = _ExportWorld(
            organization,
            workspace,
            user,
            settings,
            monkeypatch,
            tag=tag,
            instrumentation="traceai",
            collector=f"{_COLLECTOR}{slashes}",
        ).launch()
        assert (
            world.mark["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
            == f"{_COLLECTOR}/v1/traces"
        )
        assert world.mark["FI_BASE_URL"] == _COLLECTOR
        assert "collector.example.com" in world.domains
        assert world.export_log() == []


# Each refusal: the world's options, the reason word, and the level it is logged at.
_REFUSALS = {
    "snapshot_not_listed": (
        {"snapshot_setting": "some-other-snapshot"},
        "snapshot_unsupported",
        "info",
    ),
    "snapshot_setting_empty": (
        {"snapshot_setting": ""},
        "snapshot_unsupported",
        "info",
    ),
    "unbound": ({"bind": "none"}, "not_bound", "info"),
    "metadata_id_not_a_uuid": ({"bind": "bad_id"}, "not_bound", "info"),
    "agent_of_another_organisation": ({"bind": "other_org"}, "not_bound", "info"),
    "agent_deleted": ({"bind": "deleted"}, "not_bound", "info"),
    "row_disabled": ({"provider": "disabled"}, "observability_off", "info"),
    "no_observability_row": ({"provider": "missing"}, "observability_off", "info"),
    "project_deleted": ({"project": "deleted"}, "observability_off", "info"),
    "hidden_project_without_workspace": (
        {"hidden": "no_workspace"},
        "no_workspace",
        "warning",
    ),
    "hidden_project_cannot_be_made": (
        {"hidden": "raises"},
        "SimulationProjectError",
        "warning",
    ),
    "hosted_agent": ({"kind": "hosted"}, "not_source", "info"),
    "no_collector": ({"collector": None}, "no_collector", "warning"),
    "collector_without_a_host": (
        {"collector": "https://:8443"},
        "no_collector",
        "warning",
    ),
    "collector_without_a_scheme": (
        {"collector": "collector.example.com"},
        "no_collector",
        "warning",
    ),
    "otel_declares_traces_endpoint": (
        {"declared": {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://theirs.example"}},
        "customer_configuration",
        "warning",
    ),
    "provider_row_deleted": ({"provider": "deleted"}, "observability_off", "info"),
    "project_of_another_organisation": (
        {"project": "other_org"},
        "observability_off",
        "info",
    ),
    "otel_declares_traces_endpoint_in_lower_case": (
        {"declared": {"otel_exporter_otlp_traces_endpoint": "https://theirs.example"}},
        "customer_configuration",
        "warning",
    ),
    "otel_declares_endpoint": (
        {"declared": {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://theirs.example"}},
        "customer_configuration",
        "warning",
    ),
    "traceai_declares_traces_endpoint": (
        {
            "instrumentation": "traceai",
            "declared": {
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://theirs.example"
            },
        },
        "customer_configuration",
        "warning",
    ),
    "otel_declares_ingest_api_key": (
        {"declared": {"FI_SIMULATION_INGEST_API_KEY": "theirs"}},
        "customer_configuration",
        "warning",
    ),
    "otel_declares_ingest_secret_key_in_lower_case": (
        {"declared": {"fi_simulation_ingest_secret_key": "theirs"}},
        "customer_configuration",
        "warning",
    ),
    "otel_declares_another_ingest_name": (
        {"declared": {"FI_SIMULATION_INGEST_OTHER": "theirs"}},
        "customer_configuration",
        "warning",
    ),
    "traceai_declares_ingest_secret_key": (
        {
            "instrumentation": "traceai",
            "declared": {"FI_SIMULATION_INGEST_SECRET_KEY": "theirs"},
        },
        "customer_configuration",
        "warning",
    ),
    "traceai_declares_ingest_api_key_in_lower_case": (
        {
            "instrumentation": "traceai",
            "declared": {"fi_simulation_ingest_api_key": "theirs"},
        },
        "customer_configuration",
        "warning",
    ),
    "traceai_declares_base_url": (
        {
            "instrumentation": "traceai",
            "declared": {"FI_BASE_URL": "https://theirs.example"},
        },
        "customer_configuration",
        "warning",
    ),
    "traceai_declares_endpoint": (
        {
            "instrumentation": "traceai",
            "declared": {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://theirs.example"},
        },
        "customer_configuration",
        "warning",
    ),
    "not_instrumented": ({"instrumentation": "none"}, "not_instrumented", "info"),
    "export_key_disabled": ({"credentials": "disabled"}, "key_disabled", "warning"),
    "key_resolver_raises": ({"credentials": "raises"}, "no_key", "warning"),
    "key_resolver_answers_nothing": ({"credentials": "none"}, "no_key", "warning"),
    "key_without_its_api_key": ({"credentials": "no_api_key"}, "no_key", "warning"),
    "key_without_its_secret_key": (
        {"credentials": "no_secret_key"},
        "no_key",
        "warning",
    ),
    "summary_raises": ({"summary_raises": True}, "RuntimeError", "warning"),
    "egress_cap": ({"cap_at_baseline": True}, "egress_cap", "warning"),
    "summary_module_missing": (
        {"library_missing": ("simulate.services.agent_setup",)},
        "ModuleNotFoundError",
        "warning",
    ),
    "key_module_missing": (
        {"library_missing": ("tracer.services.collector_ingest",)},
        "ModuleNotFoundError",
        "warning",
    ),
}


# The reasons decided before the hidden project is asked for.
_BEFORE_THE_HIDDEN_PROJECT = {
    "snapshot_unsupported",
    "not_bound",
    "observability_off",
    "not_source",
}


@pytest.mark.django_db
@pytest.mark.parametrize("case", sorted(_REFUSALS))
def test_a_refused_export_leaves_the_mark_and_launches(
    case, organization, workspace, user, settings, monkeypatch
):
    options, reason, level = _REFUSALS[case]
    world = _ExportWorld(
        organization, workspace, user, settings, monkeypatch, tag=case[:12], **options
    ).launch()
    assert world.export_log() == [
        (level, {"job_id": str(world.job.id), "reason": reason})
    ]
    # The customer's own secrets reach the sandbox untouched, and nothing of ours does.
    _assert_launched_without_export(world, declared_secrets=world.declared)
    assert set(world.refs) == set(world.declared)
    if reason in _BEFORE_THE_HIDDEN_PROJECT or case in {
        "summary_raises",
        "summary_module_missing",
    }:
        # A run refused before that step must not have made the project.
        assert world.hidden_calls == []
        assert world.hidden is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "collector",
    [
        "http://localhost:8000",
        "http://host.docker.internal:8000",
        "http://10.0.0.3:8000",
        "https://s3cret-user:s3cret-pass@collector.example.com",
    ],
)
def test_an_unsafe_collector_address_is_refused_before_a_key_is_resolved(
    collector, organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="unsafe",
        instrumentation="traceai",
        collector=collector,
    ).launch()
    assert world.export_log() == [
        ("warning", {"job_id": str(world.job.id), "reason": "no_collector"})
    ]
    # Nothing is read or written for a run that cannot export.
    assert world.credential_calls == []
    _assert_launched_without_export(world)
    assert urlparse(collector).hostname not in world.domains
    assert b"s3cret" not in world.uploads["/work/job.json"]
    assert "s3cret" not in world.logs.text()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "word, declared",
    [
        ("traceai", {"FI_API_KEY": "their-api-key"}),
        ("traceai", {"FI_SECRET_KEY": "their-secret-key"}),
        (
            "traceai",
            {"FI_API_KEY": "their-api-key", "FI_SECRET_KEY": "their-secret-key"},
        ),
        (
            "opentelemetry",
            {"FI_API_KEY": "their-api-key", "FI_SECRET_KEY": "their-secret-key"},
        ),
    ],
)
def test_declared_fi_keys_stay_as_declared_and_our_pair_ships_beside_them(
    word, declared, organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="theirs",
        instrumentation=word,
        declared=declared,
    ).launch()
    assert world.export_log() == []
    assert world.mark["FI_SIMULATION_EXPORT"] == "our_keys"
    # Their values reach the process untouched, whether one name, both or neither is
    # declared, and no alias of ours takes their names.
    assert world.secrets == {**declared, **_INGEST_PAIR}
    assert {
        alias
        for alias, ref in world.refs.items()
        if ref["purpose"] == "observability_export"
    } == set(_INGEST_PAIR)
    assert all(world.refs[alias]["purpose"] == "target_provider" for alias in declared)


@pytest.mark.django_db
@pytest.mark.parametrize("word", ["opentelemetry", "traceai"])
@pytest.mark.parametrize(
    "name", ["OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_HEADERS"]
)
def test_a_declared_otlp_headers_alias_no_longer_refuses_the_export(
    word, name, organization, workspace, user, settings, monkeypatch
):
    declared = {name: "x-theirs=1"}
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="headers",
        instrumentation=word,
        declared=declared,
    ).launch()
    assert world.export_log() == []
    assert world.mark["FI_SIMULATION_EXPORT"] == "our_keys"
    # Their header stays as declared; the hook, not the launcher, merges it.
    assert world.secrets == {**declared, **_INGEST_PAIR}


def test_the_export_plan_does_not_print_its_keys():
    from simulate.services.hosted_harness_gateway import _ExportPlan

    plan = _ExportPlan(
        project_name="fi-simulation-agent",
        collector_url=_COLLECTOR,
        export="our_keys",
        instrumentation="traceai",
        secrets=tuple(_INGEST_PAIR.items()),
        redaction_values=(_EXPORT_API_KEY, _EXPORT_SECRET_KEY),
    )
    text = repr(plan)
    assert _EXPORT_API_KEY not in text
    assert _EXPORT_SECRET_KEY not in text


def test_authoring_and_chat_marks_are_computed_before_the_sandbox_is_created(
    monkeypatch,
):
    events = []
    monkeypatch.setattr(
        "tracer.services.simulation_mark.customer_code_environment",
        lambda **_kwargs: events.append(("mark", None)) or {},
    )
    for upload_document in (_authoring_document, _chat_document):
        events.clear()
        upload_document(monkeypatch, events)
        names = [name for name, _ in events]
        assert names.count("mark") == 1
        assert names.index("mark") < names.index("create")


@pytest.mark.django_db
def test_a_failure_after_the_key_leaves_no_alias_ref_or_host(
    organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="halfway",
        instrumentation="traceai",
        credentials="fails_after_the_keys",
    )
    world.launch()
    # Both key values had been read when the enabled check failed.
    assert world.credential_calls
    assert world.export_log() == [
        ("warning", {"job_id": str(world.job.id), "reason": "RuntimeError"})
    ]
    _assert_launched_without_export(world)
    assert set(_INGEST_PAIR).isdisjoint(world.secrets)
    redacted = cached_attempt_redaction_values(world.attempt.id)
    assert not any(_EXPORT_API_KEY in value for value in redacted)


@pytest.mark.django_db
def test_a_collector_host_already_allowed_adds_nothing_to_the_egress_list(
    organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="listed",
        base_domains=("ingest.example.com", "collector.example.com"),
        cap_at_baseline=True,
    ).launch()
    # The list was already at the provider's cap, and the host was on it.
    assert world.export_log() == []
    assert world.mark["FI_SIMULATION_EXPORT"] == "our_keys"
    assert len(world.domains) == world.client.max_egress_domains


@pytest.mark.django_db
def test_a_collector_host_past_the_egress_cap_is_dropped_and_the_launch_goes_on(
    organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="cap",
        cap_at_baseline=True,
    ).launch()
    assert world.export_log() == [
        ("warning", {"job_id": str(world.job.id), "reason": "egress_cap"})
    ]
    assert len(world.domains) == world.client.max_egress_domains
    _assert_launched_without_export(world)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "customer_domains, code",
    [
        ([f"host{n}.example.com" for n in range(21)], "egress_domain_limit_exceeded"),
        (["10.0.0.5"], None),
    ],
)
def test_the_customers_own_egress_validation_stays_fail_closed_with_the_export_on(
    customer_domains, code, organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag="closed",
        customer_domains=customer_domains,
    )
    with pytest.raises(HostedHarnessError) as raised:
        world.launch()
    if code:
        assert raised.value.code == code
    # Refused before any decision: no hidden project, no key, no sandbox.
    assert world.credential_calls == []
    assert world.hidden_calls == []
    assert world.events == []


_BAD_MARKS = {
    "raises": (RuntimeError(_LEAK_SENTINEL), "raised"),
    "not_a_dict": (["OTEL_RESOURCE_ATTRIBUTES"], "invalid"),
    "none": (None, "invalid"),
    "otlp_headers": ({"OTEL_EXPORTER_OTLP_HEADERS": "x=y"}, "invalid"),
    "otlp_traces_headers": ({"OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x=y"}, "invalid"),
    "fi_api_key": ({"FI_API_KEY": "a-key"}, "invalid"),
    "fi_secret_key": ({"FI_SECRET_KEY": "a-secret"}, "invalid"),
    "ingest_api_key": ({"FI_SIMULATION_INGEST_API_KEY": "a-key"}, "invalid"),
    "ingest_secret_key": ({"FI_SIMULATION_INGEST_SECRET_KEY": "a-secret"}, "invalid"),
    "eleven_entries": ({f"OTEL_NAME_{n}": "v" for n in range(11)}, "invalid"),
    "foreign_name": ({"HTTP_PROXY": "http://x"}, "invalid"),
    "lowercase_name": ({"otel_resource_attributes": "x"}, "invalid"),
    "base_url_with_suffix": ({"FI_BASE_URL_EXTRA": "x"}, "invalid"),
    "value_not_text": ({"OTEL_RESOURCE_ATTRIBUTES": 1}, "invalid"),
    "value_too_long": ({"OTEL_RESOURCE_ATTRIBUTES": "a" * 2049}, "invalid"),
    "value_not_ascii": ({"OTEL_RESOURCE_ATTRIBUTES": "café"}, "invalid"),
    "value_has_newline": ({"OTEL_RESOURCE_ATTRIBUTES": "a\nb"}, "invalid"),
}


@pytest.mark.django_db
@pytest.mark.parametrize("case", sorted(_BAD_MARKS))
def test_bad_or_raising_mark_is_logged_and_the_launch_goes_on_unmarked(
    case, organization, workspace, user, settings, monkeypatch
):
    outcome, reason = _BAD_MARKS[case]

    def function(**_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    world = _ExportWorld(
        organization,
        workspace,
        user,
        settings,
        monkeypatch,
        tag=case[:12],
        instrumentation="traceai",
    )
    monkeypatch.setattr(
        "tracer.services.simulation_mark.customer_code_environment", function
    )
    world.launch()
    assert world.logs.events("simulation_mark_unavailable") == [
        ("warning", {"job_id": str(world.job.id), "reason": reason})
    ]
    assert world.mark == {}
    # The key travels with the dictionary that says what to do with it, or not at all.
    assert world.secrets == {}
    assert not any(
        ref.get("purpose") == "observability_export" for ref in world.refs.values()
    )
    assert "collector.example.com" not in world.domains
    assert world.attempt.state == HostedHarnessAttempt.State.RUNNING
    assert _EXPORT_API_KEY.encode() not in world.uploads["/work/job.json"]
    assert _LEAK_SENTINEL not in world.logs.text()


@pytest.mark.django_db
def test_an_empty_dictionary_from_the_function_passes_and_is_not_logged(
    organization, workspace, user, settings, monkeypatch
):
    world = _ExportWorld(
        organization, workspace, user, settings, monkeypatch, tag="empty"
    )
    monkeypatch.setattr(
        "tracer.services.simulation_mark.customer_code_environment",
        lambda **_kwargs: {},
    )
    world.launch()
    assert world.logs.events("simulation_mark_unavailable") == []
    assert world.mark == {}
    # An empty dictionary is a valid answer, so the decision it came with stands.
    assert world.secrets == _INGEST_PAIR


@pytest.mark.parametrize(
    "mark, accepted",
    [
        ({}, True),
        ({"OTEL_RESOURCE_ATTRIBUTES": "a=b"}, True),
        (
            {"FI_SIMULATION_EXPORT": "our_keys", "FI_BASE_URL": "https://x.example"},
            True,
        ),
        ({f"OTEL_NAME_{n}": "v" for n in range(10)}, True),
        ({f"OTEL_NAME_{n}": "v" for n in range(11)}, False),
        ({"OTEL_X": "a" * 2048}, True),
        ({"OTEL_X": "a" * 2049}, False),
        ({"FI_BASE_URLX": "x"}, False),
        ({"FI_API_KEY": "x"}, False),
        ({"FI_SECRET_KEY": "x"}, False),
        ({"OTEL_EXPORTER_OTLP_HEADERS": "x"}, False),
        ({"OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x"}, False),
        ({"FI_SIMULATION_INGEST_API_KEY": "x"}, False),
        ({"FI_SIMULATION_INGEST_SECRET_KEY": "x"}, False),
        ({"FI_SIMULATION_INGEST_OTHER": "x"}, True),
        ({"OTEL_X": b"bytes"}, False),
        ({1: "x"}, False),
        ("OTEL_X=1", False),
    ],
)
def test_the_mark_check_accepts_only_the_documented_shape(mark, accepted):
    from simulate.services.hosted_harness_gateway import _mark_is_acceptable

    assert _mark_is_acceptable(mark) is accepted


@pytest.mark.django_db
def test_diagnostics_redaction_fallback_holds_every_ingest_key_switched_off_deleted_or_not(
    organization, workspace, user, monkeypatch
):
    from accounts.models.organization import Organization
    from accounts.models.user import OrgApiKey
    from simulate.services.hosted_harness_diagnostics import (
        forget_attempt_redaction_values,
    )

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=f"redact-{uuid.uuid4().hex[:8]}"
    )
    attempt = register_attempt(
        job.id, endpoint_base_url="https://platform.example"
    ).attempt
    forget_attempt_redaction_values(attempt.id)
    ingest = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, name="ingest", type="ingest"
    )
    switched_off = OrgApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        name="ingest off",
        type="ingest",
        enabled=False,
    )
    deleted = OrgApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="ingest gone",
        type="ingest",
    )
    # Soft-deleted the way the keys view does it, so every manager but the base one hides it.
    OrgApiKey._base_manager.filter(id=deleted.id).update(deleted=True)
    ordinary = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, name="ordinary", type="user"
    )
    stranger = OrgApiKey.no_workspace_objects.create(
        organization=Organization.objects.create(name="Key owner org"),
        workspace=None,
        name="ingest elsewhere",
        type="ingest",
    )
    before = OrgApiKey._base_manager.filter(type="ingest").count()
    seen = {}

    def poll(_attempt, _sandbox, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(f"{_GATEWAY}.poll_sandbox_diagnostics", poll)
    HostedHarnessGateway._capture_diagnostics(attempt, object(), final=True)

    values = set(seen["secret_values"])
    assert {ingest.api_key, ingest.secret_key} <= values
    # Switching a key off stops the export, but a running sandbox may still hold it.
    assert {switched_off.api_key, switched_off.secret_key} <= values
    # So may a key deleted after the launch.
    assert {deleted.api_key, deleted.secret_key} <= values
    assert not {ordinary.api_key, ordinary.secret_key} & values
    # Another organisation's key is not this job's to hold.
    assert not {stranger.api_key, stranger.secret_key} & values
    # Read, never created.
    assert OrgApiKey._base_manager.filter(type="ingest").count() == before


class _StoredObject(io.BytesIO):
    def release_conn(self):
        pass


class _MemoryStorage:
    """Object storage kept in memory; the suite has no reachable bucket."""

    def __init__(self):
        self.objects = {}

    def bucket_exists(self, _bucket):
        return False

    def make_bucket(self, _bucket):
        pass

    def set_bucket_policy(self, _bucket, _policy):
        pass

    def put_object(self, bucket_name, object_name, data, length, content_type=None):
        self.objects[object_name] = data.read(length)

    def get_object(self, _bucket, object_name):
        return _StoredObject(self.objects[object_name])


@pytest.fixture
def memory_storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _MemoryStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _write_metadata(job_id, **keys):
    """Write metadata keys the way a path other than the caller's job object would."""
    row = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    payload = dict(row.payload)
    payload["metadata"] = {**(payload.get("metadata") or {}), **keys}
    row.payload = payload
    row.save(update_fields=["payload", "updated_at"])


def _stored_metadata(job_id):
    return HostedHarnessJob.no_workspace_objects.get(id=job_id).payload["metadata"]


def _authoring_archive():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        body = b"{}"
        member = tarfile.TarInfo("contract.json")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    return out.getvalue()


@pytest.mark.django_db
def test_a_stale_progress_save_keeps_the_observability_refusal(
    organization, memory_storage
):
    from simulate.services.hosted_harness_gateway import (
        _record_harness_spend,
        store_authoring_archive,
    )

    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="stale-progress-save"
    )
    refusal = {"code": "agent_not_stored", "details": {}}

    # The poll read the job before the provision wrote the reason.
    stale = HostedHarnessJob.no_workspace_objects.get(id=job.id)
    _write_metadata(job.id, observability_refusal=refusal)
    key = store_authoring_archive(stale, _authoring_archive(), advance_lifecycle=False)

    metadata = _stored_metadata(job.id)
    assert metadata["observability_refusal"] == refusal
    assert metadata["authoring_object_key"] == key
    assert stale.payload["metadata"]["authoring_object_key"] == key

    stale = HostedHarnessJob.no_workspace_objects.get(id=job.id)
    newer = {"code": "bound", "details": {}}
    # A second attempt's spend lands after the poll read the job.
    other = {"total_usd": 0.5, "unpriced_turns": 0, "stages": []}
    _write_metadata(
        job.id,
        observability_refusal=newer,
        harness_spend={"total_usd": 0.5, "unpriced_turns": 0, "attempts": {"2": other}},
    )
    _record_harness_spend(stale, {"total_usd": 0.1, "stages": []}, 1)

    metadata = _stored_metadata(job.id)
    assert metadata["observability_refusal"] == newer
    assert sorted(metadata["harness_spend"]["attempts"]) == ["1", "2"]
    assert metadata["harness_spend"]["attempts"]["2"] == other
    assert metadata["harness_spend"]["total_usd"] == 0.6
    assert metadata["authoring_object_key"] == key


@pytest.mark.django_db
def test_a_stale_adjustment_save_keeps_the_observability_refusal(organization):
    first = {"adjustment_id": "adj-1", "status": "pending", "instruction": "one"}
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key="stale-adjustment-save"
    )
    _write_metadata(job.id, adjustments=[first])
    stale = HostedHarnessJob.no_workspace_objects.get(id=job.id)

    # A refusal and a second adjustment land after the poll read the job.
    refusal = {"code": "bound", "details": {}}
    second = {"adjustment_id": "adj-2", "status": "pending", "instruction": "two"}
    _write_metadata(job.id, observability_refusal=refusal, adjustments=[first, second])
    status_line = json.dumps({"adjustment_id": "adj-1", "status": "applied"})
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(
            download_file=lambda path, timeout=None: status_line.encode()
        )
    )

    HostedHarnessGateway._sync_adjustment_progress(stale, sandbox)

    metadata = _stored_metadata(job.id)
    assert metadata["observability_refusal"] == refusal
    assert [
        (item["adjustment_id"], item["status"]) for item in metadata["adjustments"]
    ] == [
        ("adj-1", "applied"),
        ("adj-2", "pending"),
    ]

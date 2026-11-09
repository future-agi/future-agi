"""Durable setup cooldown and provider-scoped recording credentials."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock

import pytest
import requests
from django.db import close_old_connections
from django.utils import timezone
from structlog.testing import capture_logs

from simulate.models.agent_definition import AgentDefinition, ProviderCredentials
from simulate.models.agent_version import AgentVersion
from tracer.models.observability_provider import ObservabilityProvider, ProviderChoices
from tracer.serializers.observability_provider import ObservabilityProviderSerializer
from tracer.services.observability_providers import (
    ObservabilityService,
    RetellConfigurationError,
)
from tracer.tests.test_retell_poll_orchestration import _page
from tracer.utils import observability_provider as op
from tracer.utils.vapi_recording import VapiRecordingService

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def provider(observe_project, organization, workspace):
    return ObservabilityProvider.objects.create(
        project=observe_project,
        organization=organization,
        workspace=workspace,
        provider=ProviderChoices.RETELL,
        enabled=True,
        last_fetched_at=timezone.now() - timedelta(days=1),
        poll_state={
            "retell": {"bootstrapped": True, "window": {"sentinel": "preserved"}},
            "other": {"sentinel": "preserved"},
        },
    )


def attach_agent(provider, **overrides):
    values = {
        "agent_name": "Synthetic voice agent",
        "agent_type": "voice",
        "provider": provider.provider,
        "organization": provider.organization,
        "workspace": provider.workspace,
        "inbound": True,
        "observability_provider": provider,
        "assistant_id": "synthetic-assistant",
        "api_key": "synthetic-key",
    }
    values.update(overrides)
    return AgentDefinition.objects.create(**values)


def poll(provider, **kwargs):
    return op.fetch_logs_for_provider(
        provider.id, scheduled=True, start_time=None, end_time=None, **kwargs
    )


@pytest.mark.parametrize(
    "reason",
    [
        "missing_agent",
        "deleted_agent",
        "missing_key",
        "missing_assistant_id",
        "credential_unreadable",
    ],
)
def test_setup_backoff_survives_reload_and_logs_only_transition(
    provider, monkeypatch, reason
):
    if reason != "missing_agent":
        agent = attach_agent(
            provider,
            api_key=None if reason == "missing_key" else "synthetic-key",
            assistant_id=""
            if reason == "missing_assistant_id"
            else "synthetic-assistant",
            deleted=reason == "deleted_agent",
        )
        if reason == "credential_unreadable":
            ProviderCredentials.objects.create(
                agent_definition=agent, provider_type="retell", api_key="enc::invalid"
            )
    now = timezone.now()
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    http = Mock(
        side_effect=AssertionError("incomplete setup must not call provider HTTP")
    )
    monkeypatch.setattr("tracer.services.observability_providers.requests.post", http)
    before, watermark = provider.poll_state.copy(), provider.last_fetched_at
    with capture_logs() as logs:
        assert poll(provider) is None
        provider.refresh_from_db()
        health = provider.poll_state["configuration_health"]
        assert health["status"] == "misconfigured"
        assert health["reason_code"] == reason
        assert health["failure_count"] == 1
        assert op._parse(health["next_retry_at"]) == now + timedelta(minutes=10)
        for _ in range(3):
            assert poll(provider) == op.StoreOutcome(0, 0, 0)
        now += timedelta(minutes=10)
        assert poll(provider) is None
        provider.refresh_from_db()
        assert provider.poll_state["configuration_health"]["failure_count"] == 2
        assert op._parse(
            provider.poll_state["configuration_health"]["next_retry_at"]
        ) == now + timedelta(minutes=20)
    transitions = [
        entry for entry in logs if entry["event"] == "retell_configuration_changed"
    ]
    assert len(transitions) == 1
    assert transitions[0]["log_level"] == (
        "error" if reason == "credential_unreadable" else "warning"
    )
    assert provider.poll_state["retell"] == before["retell"]
    assert provider.poll_state["other"] == before["other"]
    assert provider.last_fetched_at == watermark
    assert provider.enabled
    serialized = ObservabilityProviderSerializer(provider).data
    assert serialized["configuration_health"]["reason_code"] == reason
    assert "synthetic-key" not in str(logs) + str(serialized)
    assert "enc::invalid" not in str(logs) + str(serialized)
    http.assert_not_called()


def test_repaired_credentials_resume_before_cooldown_without_resetting_window(
    provider, monkeypatch
):
    agent = attach_agent(provider, api_key=None)
    poll(provider)
    provider.refresh_from_db()
    window = op._new_window(
        timezone.now() - timedelta(hours=2),
        timezone.now() - timedelta(hours=1),
        opened_at_hint=False,
        narrowed=False,
    )
    provider.poll_state["retell"] = {"bootstrapped": True, "window": window}
    provider.save(update_fields=["poll_state"])
    first_fetch = Mock(return_value=_page())
    monkeypatch.setattr(ObservabilityService, "fetch_retell_page", first_fetch)
    agent.api_key = "repaired-synthetic-key"
    agent.save(update_fields=["api_key"])
    with capture_logs() as logs:
        assert poll(provider) == op.StoreOutcome(0, 0, 0)
    provider.refresh_from_db()
    assert "configuration_health" not in provider.poll_state
    assert first_fetch.call_args.args[1:3] == (
        op._parse(window["start"]),
        op._parse(window["end"]),
    )
    assert any(entry.get("status") == "configured" for entry in logs)
    assert (
        ObservabilityProviderSerializer(provider).data["configuration_health"] is None
    )


def test_reason_change_and_backoff_cap(provider, monkeypatch):
    now = timezone.now()
    monkeypatch.setattr(op.timezone, "now", lambda: now)
    with capture_logs() as logs:
        poll(provider)
        attach_agent(provider, api_key=None)
        poll(provider)  # missing_agent -> missing_key, even during cooldown
        for _ in range(9):
            provider.refresh_from_db()
            now = op._parse(
                provider.poll_state["configuration_health"]["next_retry_at"]
            )
            poll(provider)
    provider.refresh_from_db()
    health = provider.poll_state["configuration_health"]
    assert op._parse(health["next_retry_at"]) == now + timedelta(hours=6)
    assert (
        len(
            [
                entry
                for entry in logs
                if entry["event"] == "retell_configuration_changed"
            ]
        )
        == 2
    )


def test_manual_and_internal_errors_do_not_quarantine(provider, monkeypatch):
    before = provider.poll_state.copy()
    with capture_logs() as logs:
        op.fetch_logs_for_provider(
            provider.id,
            scheduled=False,
            start_time=timezone.now() - timedelta(hours=1),
            end_time=timezone.now(),
        )
    provider.refresh_from_db()
    assert provider.poll_state == before
    assert any(entry["event"] == "retell_configuration_error" for entry in logs)
    monkeypatch.setattr(
        op,
        "_poll_retell_provider",
        Mock(side_effect=RetellConfigurationError("invalid window")),
    )
    with capture_logs() as logs:
        poll(provider)
    provider.refresh_from_db()
    assert provider.poll_state == before
    assert logs[-1]["log_level"] == "error"
    assert logs[-1]["reason_code"] == "invalid_poll_arguments"


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_real_provider_failures_still_log_errors(provider, monkeypatch, status):
    error = requests.HTTPError(response=Mock(status_code=status))
    monkeypatch.setattr(op, "_poll_retell_provider", Mock(side_effect=error))
    with capture_logs() as logs:
        poll(provider)
    assert logs[-1]["log_level"] == "error"
    assert logs[-1]["status_code"] == status
    provider.refresh_from_db()
    assert "configuration_health" not in provider.poll_state


@pytest.mark.django_db(transaction=True)
def test_two_workers_record_only_one_transition(provider, monkeypatch):
    logger = Mock()
    monkeypatch.setattr(op, "logger", logger)

    def record():
        close_old_connections()
        try:
            op._record_retell_configuration_failure(provider.id, "missing_agent")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: record(), range(2)))
    provider.refresh_from_db()
    assert provider.poll_state["configuration_health"]["failure_count"] == 1
    assert logger.warning.call_count == 1
    assert op._write_retell_state(provider.id, {"bootstrapped": True})
    provider.refresh_from_db()
    assert provider.poll_state["configuration_health"]["reason_code"] == "missing_agent"
    assert provider.poll_state["other"] == {"sentinel": "preserved"}


@pytest.mark.parametrize("versioned", [False, True])
def test_vapi_poll_and_every_recording_use_same_provider_key(
    provider, monkeypatch, versioned
):
    provider.provider = ProviderChoices.VAPI
    provider.save(update_fields=["provider"])
    agent = attach_agent(provider)
    expected = "synthetic-key"
    if versioned:
        version = AgentVersion.objects.create(
            agent_definition=agent,
            organization=provider.organization,
            workspace=provider.workspace,
            commit_message="Synthetic version",
            version_number=1,
            status="active",
        )
        ProviderCredentials.objects.create(
            agent_version=version, provider_type="vapi", api_key="enc::synthetic"
        )
        monkeypatch.setattr(
            ProviderCredentials, "get_api_key", lambda self: "version-key"
        )
        expected = "version-key"
    # A second provider sharing the project must never supply this one's key.
    other = ObservabilityProvider.objects.create(
        project=provider.project,
        organization=provider.organization,
        workspace=provider.workspace,
        provider="vapi",
    )
    attach_agent(other, api_key="wrong-project-key")
    response = Mock()
    response.json.return_value = [{"id": "synthetic-call"}]
    listing = Mock(return_value=response)
    monkeypatch.setattr("tracer.services.observability_providers.requests.get", listing)
    seen = []

    def normalize(log, *, api_key, project_id):
        from tracer.utils.vapi import _VAPI_RECORDING_KEYS, _rehost_recording_urls_sync

        attrs = dict.fromkeys(
            _VAPI_RECORDING_KEYS,
            "https://recordings.example.invalid/audio?token=synthetic",
        )
        _rehost_recording_urls_sync(log, attrs, api_key=api_key, project_id=project_id)
        return {}  # storage/collector is not the credential boundary under test

    def rehost(**kwargs):
        seen.append(kwargs["api_key"])
        return kwargs["audio_url"], 0

    monkeypatch.setattr(op, "normalize_vapi_data", normalize)
    monkeypatch.setattr("tracer.utils.vapi.convert_audio_url_to_s3_sync", rehost)
    poll(provider)
    assert listing.call_args.kwargs["headers"]["Authorization"] == f"Bearer {expected}"
    assert seen == [expected] * 4


def test_versioned_vapi_never_resurrects_plaintext_key(provider):
    provider.provider = "vapi"
    agent = attach_agent(provider, api_key="obsolete-plaintext")
    AgentVersion.objects.create(
        agent_definition=agent,
        organization=provider.organization,
        workspace=provider.workspace,
        commit_message="Synthetic version",
        version_number=1,
    )
    assert VapiRecordingService._api_key_from_agent_definition(agent) is None


def test_preversioned_encrypted_credential_is_authoritative(provider, monkeypatch):
    provider.provider = "vapi"
    agent = attach_agent(provider, api_key="obsolete-plaintext")
    ProviderCredentials.objects.create(
        agent_definition=agent, provider_type="vapi", api_key="enc::synthetic"
    )
    monkeypatch.setattr(
        ProviderCredentials, "get_api_key", lambda self: "encrypted-key"
    )
    assert VapiRecordingService._api_key_from_agent_definition(agent) == "encrypted-key"
    monkeypatch.setattr(ProviderCredentials, "get_api_key", lambda self: "")
    assert VapiRecordingService._api_key_from_agent_definition(agent) is None


@pytest.mark.parametrize("explicit_key", [None, ""])
def test_missing_vapi_key_does_not_fall_back_to_public_download(
    provider, monkeypatch, explicit_key
):
    provider.provider = "vapi"
    attach_agent(provider, api_key=None)
    normalizer = Mock()
    monkeypatch.setattr(op, "normalize_vapi_data", normalizer)
    with pytest.raises(ValueError, match="not configured"):
        op.process_and_store_logs(
            [{"id": "synthetic-call"}], provider, api_key=explicit_key
        )
    normalizer.assert_not_called()


def test_bad_provider_does_not_stop_healthy_provider(provider, monkeypatch):
    healthy = ObservabilityProvider.objects.create(
        project=provider.project,
        organization=provider.organization,
        workspace=provider.workspace,
        provider="retell",
        enabled=True,
    )
    attach_agent(healthy)
    response = Mock(status_code=200)
    response.json.return_value = {"items": [], "has_more": False}
    post = Mock(return_value=response)
    monkeypatch.setattr("tracer.services.observability_providers.requests.post", post)
    op.fetch_observability_logs._original_func()
    provider.refresh_from_db()
    healthy.refresh_from_db()
    assert provider.poll_state["configuration_health"]["reason_code"] == "missing_agent"
    assert healthy.last_fetched_at is not None
    assert "configuration_health" not in healthy.poll_state
    assert post.call_count == 1


def test_vapi_active_version_beats_newer_draft_and_rotation_is_seen(
    provider, monkeypatch
):
    provider.provider = "vapi"
    agent = attach_agent(provider, api_key="stale-key")
    versions = [
        AgentVersion.objects.create(
            agent_definition=agent,
            organization=provider.organization,
            workspace=provider.workspace,
            version_number=number,
            status=status,
            commit_message="Synthetic version",
        )
        for number, status in [(1, "active"), (2, "draft")]
    ]
    for version in versions:
        ProviderCredentials.objects.create(
            agent_version=version,
            provider_type="vapi",
            api_key=f"enc::key-{version.version_number}",
        )
    monkeypatch.setattr(
        ProviderCredentials,
        "get_api_key",
        lambda self: self.api_key.removeprefix("enc::"),
    )
    assert VapiRecordingService._api_key_from_agent_definition(agent) == "key-1"
    ProviderCredentials.objects.filter(agent_version=versions[0]).update(
        api_key="enc::rotated"
    )
    assert VapiRecordingService._api_key_from_agent_definition(agent) == "rotated"
    versions[0].status = "archived"
    versions[0].save(update_fields=["status"])
    assert VapiRecordingService._api_key_from_agent_definition(agent) == "key-2"


def test_unreadable_vapi_credential_never_uses_plaintext(provider, monkeypatch):
    provider.provider = "vapi"
    agent = attach_agent(provider, api_key="stale-key")
    ProviderCredentials.objects.create(
        agent_definition=agent, provider_type="vapi", api_key="enc::invalid"
    )
    with pytest.raises(ValueError):
        VapiRecordingService._api_key_from_agent_definition(agent)

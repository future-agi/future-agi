from unittest.mock import Mock

import pytest

from simulate.models import AgentDefinition, AgentVersion, ProviderCredentials
from tracer.models.observability_provider import ObservabilityProvider
from tracer.services import observability_providers as providers
from tracer.utils import observability_provider as poll
from tracer.utils import vapi

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def connection(observe_project):
    def create(key="definition-key", *, enabled=True):
        provider = ObservabilityProvider.objects.create(
            project=observe_project,
            organization=observe_project.organization,
            workspace=observe_project.workspace,
            provider="vapi",
            enabled=enabled,
        )
        AgentDefinition.objects.create(
            organization=provider.organization,
            workspace=provider.workspace,
            observability_provider=provider,
            agent_name="Test voice agent",
            inbound=True,
            provider="vapi",
            api_key=key,
            assistant_id="definition-assistant",
        )
        return provider

    return create


def version(agent, assistant, *, status="active", provider="vapi"):
    return AgentVersion.objects.create(
        agent_definition=agent,
        organization=agent.organization,
        workspace=agent.workspace,
        status=status,
        commit_message="Test configuration",
        configuration_snapshot={"provider": provider, "assistant_id": assistant},
    )


def credential(*, key="row-key", assistant="", provider="vapi", **links):
    return ProviderCredentials.objects.create(
        api_key=key, assistant_id=assistant, provider_type=provider, **links
    )


def test_activation_during_listing_keeps_the_pair_for_every_page_and_recording(
    connection, monkeypatch
):
    provider = connection()
    agent = provider.agent_definition
    credential(agent_version=version(agent, "active-assistant"), key="active-key")
    draft = version(agent, "draft-assistant", status="draft")
    credential(agent_version=draft, key="draft-key")
    url = "https://recordings.example.invalid/audio"
    recording = {
        "mono": {"combinedUrl": url, "assistantUrl": url, "customerUrl": url},
        "stereoUrl": url,
    }
    call = {
        "id": "call-1",
        "updatedAt": "2026-01-01T12:00:00Z",
        "artifact": {"recording": recording},
    }
    pages = iter([[call], []])

    def respond(*args, **kwargs):
        batch = next(pages)
        if batch:
            draft.activate()
        return Mock(json=lambda: batch)

    get = Mock(side_effect=respond)
    copy = Mock(side_effect=lambda **kwargs: (kwargs["audio_url"], 0))
    monkeypatch.setattr(providers, "VAPI_PAGE_LIMIT", 1)
    monkeypatch.setattr(providers.requests, "get", get)
    monkeypatch.setattr(vapi, "_extract_call_logs", Mock())
    monkeypatch.setattr(vapi, "convert_audio_url_to_s3_sync", copy)
    monkeypatch.setattr(poll, "_export_provider_call_to_collector", lambda *args: 1)

    outcome = poll._poll_other_provider(provider, start_time=None, end_time=None)
    assert outcome.stored == 1
    assert get.call_count == 2
    for request in get.call_args_list:
        assert request.kwargs["headers"]["Authorization"] == "Bearer active-key"
        assert request.kwargs["params"]["assistantId"] == "active-assistant"
    assert sorted(item.kwargs["url_type"] for item in copy.call_args_list) == [
        "mono_assistant",
        "mono_combined",
        "mono_customer",
        "stereo",
    ]
    assert all(item.kwargs["api_key"] == "active-key" for item in copy.call_args_list)


@pytest.mark.parametrize(
    "state,expected_key,expected_assistant",
    [
        ("latest", "draft-key", "row-assistant"),
        ("migrated", "legacy-key", "owner-assistant"),
        ("pre-versioned", "legacy-key", "definition-assistant"),
        ("unversioned-legacy", "legacy-key", "definition-assistant"),
        ("moved-definition", "legacy-key", "selected-assistant"),
        ("raw-empty", "definition-key", "definition-assistant"),
        ("absent", "definition-key", "definition-assistant"),
    ],
)
def test_listing_uses_the_selected_sources_pair(
    connection, monkeypatch, state, expected_key, expected_assistant
):
    provider = connection()
    agent = provider.agent_definition
    if state != "pre-versioned":
        status = "draft" if state == "latest" else "active"
        selected = version(agent, "selected-assistant", status=status)
    if state == "latest":
        draft = version(agent, "snapshot-assistant", status="draft")
        credential(agent_version=draft, key="draft-key", assistant="row-assistant")
    elif state == "migrated":
        owner = version(agent, "owner-assistant", status="archived")
        credential(agent_definition=agent, agent_version=owner, key="legacy-key")
    elif state == "moved-definition":
        agent.provider = "retell"
        agent.save(update_fields=["provider"])
        credential(
            agent_definition=agent, key="legacy-key", assistant="selected-assistant"
        )
    elif state in ("pre-versioned", "unversioned-legacy", "raw-empty"):
        credential(agent_definition=agent, key="legacy-key")
        if state == "raw-empty":
            credential(agent_version=selected, key="", assistant="unused")

    get = Mock(return_value=Mock(json=lambda: []))
    monkeypatch.setattr(providers.requests, "get", get)
    providers.ObservabilityService.get_call_logs(provider)
    get.assert_called_once()
    assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {expected_key}"
    assert get.call_args.kwargs["params"]["assistantId"] == expected_assistant


@pytest.mark.parametrize(
    "state",
    [
        "wrong-version",
        "wrong-row",
        "empty-wrong-row",
        "wrong-owner",
        "foreign-owner",
        "unmatched-legacy",
        "unreadable",
        "blank-key",
        "no-assistant",
    ],
)
def test_unusable_pair_preserves_watermark(connection, monkeypatch, state):
    provider = connection()
    agent = provider.agent_definition
    selected = version(
        agent,
        "" if state == "no-assistant" else "selected-assistant",
        provider="bland" if state == "wrong-version" else "vapi",
    )
    if state in ("wrong-owner", "foreign-owner"):
        foreign = state == "foreign-owner"
        owner = version(
            connection().agent_definition if foreign else agent,
            "owner-assistant",
            status="draft",
            provider="vapi" if foreign else "bland",
        )
        credential(agent_definition=agent, agent_version=owner)
    elif state == "unmatched-legacy":
        agent.provider = "bland"
        agent.save(update_fields=["provider"])
        credential(agent_definition=agent, assistant="another-assistant")
    else:
        credential(agent_definition=agent, key="other-legacy-key")
        keys = {"empty-wrong-row": "", "unreadable": "enc::invalid", "blank-key": " "}
        credential(
            agent_version=selected,
            key=keys.get(state, "row-key"),
            provider="retell" if state.endswith("wrong-row") else "vapi",
        )

    get = Mock(return_value=Mock(json=lambda: []))
    monkeypatch.setattr(providers.requests, "get", get)
    watermark = provider.last_fetched_at
    outcome = poll.fetch_logs_for_provider(
        provider.pk, scheduled=True, start_time=None, end_time=None
    )
    assert outcome is None
    get.assert_not_called()
    provider.refresh_from_db()
    assert provider.last_fetched_at == watermark


def test_direct_processing_uses_only_the_linked_agent(connection, monkeypatch):
    other = connection()
    other_version = version(other.agent_definition, "other-assistant")
    credential(agent_version=other_version, key="other-key")
    target = connection(enabled=False)
    normalize = Mock(return_value={})
    monkeypatch.setattr(poll, "normalize_vapi_data", normalize)
    poll.process_and_store_logs([{}], target)
    normalize.assert_called_once_with(
        {}, api_key="definition-key", project_id=str(target.project_id)
    )
    normalize.reset_mock()
    with pytest.raises(ValueError, match="Vapi API key"):
        poll.process_and_store_logs([{}], target, api_key="")

    target.agent_definition.api_key = ""
    target.agent_definition.save(update_fields=["api_key"])
    with pytest.raises(ValueError, match="Vapi API key"):
        poll.process_and_store_logs([{}], target)
    normalize.assert_not_called()

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from accounts.tests.test_rbac_regressions import _member_client, _viewer_client
from simulate.models import AgentDefinition, AgentVersion
from simulate.services.agent_definition import (
    MASKED_VALUE,
    mask_websocket_url,
    sync_provider_credentials,
)
from simulate.services.types.agent_definition import ProviderCredentialsInput

pytestmark = pytest.mark.django_db

AGENTS = "/simulate/agent-definitions/"
VALIDATE = "/simulate/api/livekit/validate-credentials/"
HEADERS = {"Authorization": "Bearer ws-secret-token", "X-Team": "support"}
SOCKET_URL = "https://agent.example.com/socket?token=url-secret&region=us"
STORED_LIVEKIT_URL = "wss://stored.livekit.example"


@pytest.fixture
def socket_agent(organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="Socket agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
        websocket_url=SOCKET_URL,
        websocket_headers=HEADERS,
    )
    agent.create_version(
        description="v1",
        commit_message="v1",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    return agent


@pytest.fixture
def livekit_agent(organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="LiveKit agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        provider="livekit",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    version = agent.create_version(
        description="v1",
        commit_message="v1",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    sync_provider_credentials(
        version,
        ProviderCredentialsInput(
            provider="livekit",
            livekit_url=STORED_LIVEKIT_URL,
            livekit_api_key="APIstoredkey123",
            livekit_api_secret="stored-secret-value-32-characters",
            livekit_agent_name="support",
            provider_was_provided=True,
        ),
    )
    return agent


def _leaks(body):
    text = str(body)
    return "ws-secret-token" in text or "url-secret" in text


@pytest.mark.parametrize("client_for", [_viewer_client, _member_client])
def test_agent_reads_name_the_headers_without_their_values(
    organization, workspace, socket_agent, client_for
):
    client = client_for(organization, workspace)

    listed = client.get(AGENTS)
    detail = client.get(f"{AGENTS}{socket_agent.id}/")

    assert listed.status_code == 200, listed.content
    assert detail.status_code == 200, detail.content
    assert not _leaks(listed.json())
    assert not _leaks(detail.json())
    assert "Authorization" in str(detail.json())


def test_saving_the_masked_values_back_keeps_the_stored_ones(
    organization, workspace, socket_agent
):
    response = _member_client(organization, workspace).put(
        f"{AGENTS}{socket_agent.id}/edit/",
        {
            "agent_name": "Socket agent",
            "agent_type": "text",
            "websocket_url": mask_websocket_url(SOCKET_URL),
            "websocket_headers": {
                "Authorization": MASKED_VALUE,
                "X-Team": MASKED_VALUE,
                "X-New": "new-value",
            },
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    socket_agent.refresh_from_db()
    assert socket_agent.websocket_url == SOCKET_URL
    assert socket_agent.websocket_headers == {**HEADERS, "X-New": "new-value"}


def _fake_livekit_api():
    fake_api = MagicMock()
    fake_api.room.create_room = AsyncMock()
    fake_api.room.delete_room = AsyncMock()
    fake_api.aclose = AsyncMock()
    return MagicMock(return_value=fake_api)


def _check_masked_credentials(organization, workspace, livekit_agent, url):
    livekit_api = _fake_livekit_api()
    with patch("livekit.api.LiveKitAPI", livekit_api):
        response = _member_client(organization, workspace).post(
            VALIDATE,
            {
                "livekit_url": url,
                "api_key": MASKED_VALUE,
                "api_secret": MASKED_VALUE,
                "agent_definition_id": str(livekit_agent.id),
            },
            format="json",
        )
    return response, livekit_api


def test_stored_livekit_credentials_go_only_to_the_stored_server(
    organization, workspace, livekit_agent
):
    response, livekit_api = _check_masked_credentials(
        organization, workspace, livekit_agent, STORED_LIVEKIT_URL
    )

    assert response.status_code == 200, response.content
    sent = livekit_api.call_args.kwargs
    assert sent["url"] == STORED_LIVEKIT_URL.replace("wss://", "https://")
    assert sent["api_key"] == "APIstoredkey123"


def test_a_check_against_a_new_server_needs_the_key_and_secret_again(
    organization, workspace, livekit_agent
):
    response, livekit_api = _check_masked_credentials(
        organization, workspace, livekit_agent, "wss://someone-else.example"
    )

    assert response.status_code == 400, response.content
    assert "API key and secret again" in str(response.data)
    livekit_api.assert_not_called()


def _edit(client, agent, **fields):
    return client.put(
        f"{AGENTS}{agent.id}/edit/",
        {"agent_name": agent.agent_name, "agent_type": agent.agent_type, **fields},
        format="json",
    )


def test_masked_headers_are_not_sent_to_a_new_host(
    organization, workspace, socket_agent
):
    response = _edit(
        _member_client(organization, workspace),
        socket_agent,
        websocket_url="https://someone-else.example/ws",
        websocket_headers={"Authorization": MASKED_VALUE},
    )

    assert response.status_code == 400, response.content
    assert "header values again" in str(response.data)
    socket_agent.refresh_from_db()
    assert socket_agent.websocket_url == SOCKET_URL
    assert socket_agent.websocket_headers == HEADERS


def test_a_masked_url_token_is_not_sent_to_a_new_host(
    organization, workspace, socket_agent
):
    response = _edit(
        _member_client(organization, workspace),
        socket_agent,
        websocket_url="https://someone-else.example/socket?token=********",
    )

    assert response.status_code == 400, response.content
    socket_agent.refresh_from_db()
    assert socket_agent.websocket_url == SOCKET_URL


def test_editing_one_url_parameter_keeps_the_masked_token(
    organization, workspace, socket_agent
):
    response = _edit(
        _member_client(organization, workspace),
        socket_agent,
        websocket_url="https://agent.example.com/socket?token=********&region=eu",
    )

    assert response.status_code == 200, response.content
    socket_agent.refresh_from_db()
    assert socket_agent.websocket_url == (
        "https://agent.example.com/socket?token=url-secret&region=eu"
    )


def test_the_operations_endpoint_keeps_masked_headers_too(
    organization, workspace, socket_agent
):
    response = _member_client(organization, workspace).patch(
        f"/simulate/api/agent-definition-operations/{socket_agent.id}/",
        {"websocket_headers": {"Authorization": MASKED_VALUE, "X-Team": "sales"}},
        format="json",
    )

    assert response.status_code == 200, response.content
    socket_agent.refresh_from_db()
    assert socket_agent.websocket_headers == {
        "Authorization": "Bearer ws-secret-token",
        "X-Team": "sales",
    }


def test_a_new_livekit_server_needs_the_key_and_secret_again(
    organization, workspace, livekit_agent
):
    client = _member_client(organization, workspace)
    moved = {
        "provider": "livekit",
        "livekit_url": "wss://someone-else.example",
        "livekit_agent_name": "support",
    }

    refused = _edit(
        client,
        livekit_agent,
        **moved,
        livekit_api_key=MASKED_VALUE,
        livekit_api_secret=MASKED_VALUE,
    )
    retyped = _edit(
        client,
        livekit_agent,
        **moved,
        livekit_api_key="APInewkey456",
        livekit_api_secret="new-secret-value-32-characters!!",
    )

    assert refused.status_code == 400, refused.content
    assert "API key and secret again" in str(refused.data)
    assert retyped.status_code == 200, retyped.content
    creds = livekit_agent.active_version.credentials
    creds.refresh_from_db()
    assert creds.server_url == "wss://someone-else.example"
    assert creds.get_api_key() == "APInewkey456"


def test_another_organizations_agent_lends_no_credentials(
    organization, workspace, livekit_agent
):
    from accounts.models.organization import Organization

    stranger_org = Organization.objects.create(name="Elsewhere")
    AgentDefinition.objects.filter(id=livekit_agent.id).update(
        organization=stranger_org
    )

    _, livekit_api = _check_masked_credentials(
        organization, workspace, livekit_agent, "wss://someone-else.example"
    )

    sent = livekit_api.call_args.kwargs
    assert sent["api_key"] == MASKED_VALUE
    assert sent["api_secret"] == MASKED_VALUE


def test_a_new_version_on_a_new_server_needs_the_key_and_secret_again(
    organization, workspace, livekit_agent
):
    response = _member_client(organization, workspace).post(
        f"{AGENTS}{livekit_agent.id}/versions/create/",
        {
            "commit_message": "Move the agent",
            "provider": "livekit",
            "livekit_url": "wss://someone-else.example",
            "livekit_api_key": MASKED_VALUE,
            "livekit_api_secret": MASKED_VALUE,
            "livekit_agent_name": "support",
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "API key and secret again" in str(response.data)
    assert AgentVersion.objects.filter(agent_definition=livekit_agent).count() == 1
    creds = livekit_agent.active_version.credentials
    assert creds.server_url == STORED_LIVEKIT_URL

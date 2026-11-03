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


def test_stored_livekit_credentials_go_only_to_the_stored_server(
    organization, workspace, livekit_agent
):
    fake_api = MagicMock()
    fake_api.room.create_room = AsyncMock()
    fake_api.room.delete_room = AsyncMock()
    fake_api.aclose = AsyncMock()
    livekit_api = MagicMock(return_value=fake_api)

    with patch("livekit.api.LiveKitAPI", livekit_api):
        response = _member_client(organization, workspace).post(
            VALIDATE,
            {
                "livekit_url": "wss://someone-else.example",
                "api_key": MASKED_VALUE,
                "api_secret": MASKED_VALUE,
                "agent_definition_id": str(livekit_agent.id),
            },
            format="json",
        )

    assert response.status_code == 200, response.content
    sent_to = livekit_api.call_args.kwargs["url"]
    assert sent_to == STORED_LIVEKIT_URL.replace("wss://", "https://")

"""Future AGI Cloud never probes a customer-supplied LiveKit URL to "test" credentials.

``POST /simulate/api/livekit/validate-credentials/`` builds a LiveKit client from
the URL, key and secret in the request and calls CreateRoom, CreateDispatch and
DeleteRoom on it from the platform backend, with redirects followed and no
destination pinning. On a hosted deployment that reaches private addresses,
loopback, link-local, cloud metadata and anything a redirect points at. On Future
AGI Cloud the endpoint sends nothing and says the credentials were not verified;
a self-hosted install keeps probing its own LiveKit server. Saving credentials
never depends on this endpoint.

DB-free: the HTTP client and the resolver are replaced by fakes that record and
refuse every call, and the voice-simulation plan gate is stubbed open.
"""

import json
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from simulate.serializers.requests.agent_definition import (
    AgentDefinitionCreateRequestSerializer,
    AgentDefinitionEditRequestSerializer,
)
from simulate.views.livekit_api import ValidateLiveKitCredentialsView

PATH = "/simulate/api/livekit/validate-credentials/"
NOT_VERIFIED = (
    "LiveKit credentials not verified on Future AGI Cloud: Future AGI does not "
    "contact customer LiveKit URLs from its servers; connection problems will "
    "show when a run connects."
)
METADATA_URL = "http://169.254.169.254/latest/meta-data/"

CUSTOMER_URLS = [
    pytest.param("wss://demo.livekit.cloud", id="public-host"),
    pytest.param("https://203.0.113.10", id="public-ip"),
    pytest.param("http://10.0.0.5:7880", id="private-ip"),
    pytest.param("ws://127.0.0.1:7880", id="loopback"),
    pytest.param("http://localhost:7880", id="loopback-name"),
    pytest.param("http://169.254.1.1", id="link-local"),
    pytest.param(METADATA_URL, id="metadata"),
    pytest.param("ws://[::1]:7880", id="ipv6-loopback"),
    pytest.param("wss://[fd00::1]", id="ipv6-private"),
    pytest.param("http://[::ffff:169.254.169.254]", id="ipv6-mapped-metadata"),
    # A public name whose server answers 302 to metadata: nothing may reach it.
    pytest.param("https://redirect.example.com", id="redirecting"),
]

# The same switch as the harness preflight (T6 #7): tfc.ee_loader.is_cloud_env.
HOSTED = ["US", "EU", "DEV", " us ", "Eu\n"]
SELF_HOSTED = ["", "self-hosted", "false"]


@pytest.fixture
def network():
    """Record every HTTP client built and every request or lookup, and refuse them.

    LiveKitAPI builds an ``aiohttp.ClientSession`` and every Twirp call goes
    through ``ClientSession._request`` (which also runs aiohttp's redirect loop).
    The socket-level fakes keep any other path off the real network.
    """
    sessions = []
    sent = []
    resolved = []
    real_session_init = aiohttp.ClientSession.__init__

    def session_init(self, *args, **kwargs):
        sessions.append(kwargs.get("base_url"))
        real_session_init(self, *args, **kwargs)

    async def request(self, method, url, *args, **kwargs):
        sent.append((method, str(url)))
        raise aiohttp.ClientConnectionError(f"test network refused {url}")

    def resolve(host, *args, **kwargs):
        resolved.append(host)
        raise socket.gaierror(f"test resolver refused {host}")

    def create_connection(address, *args, **kwargs):
        resolved.append(address)
        raise ConnectionRefusedError(f"test network refused {address}")

    with (
        patch.object(aiohttp.ClientSession, "__init__", session_init),
        patch.object(aiohttp.ClientSession, "_request", request),
        patch.object(socket, "getaddrinfo", resolve),
        patch.object(socket, "gethostbyname", resolve),
        patch.object(socket, "gethostbyname_ex", resolve),
        patch.object(socket, "create_connection", create_connection),
    ):
        yield SimpleNamespace(sessions=sessions, sent=sent, resolved=resolved)


@pytest.fixture(autouse=True)
def voice_plan_open():
    with patch("tfc.ee_gates.voice_sim_gate_response", return_value=None):
        yield


def _post(body):
    request = APIRequestFactory().post(PATH, body, format="json")
    user = SimpleNamespace(
        id=uuid.uuid4(),
        is_authenticated=True,
        organization=SimpleNamespace(id=uuid.uuid4()),
    )
    force_authenticate(request, user=user)
    return ValidateLiveKitCredentialsView.as_view()(request)


def _body(url, **overrides):
    body = {
        "livekit_url": url,
        "api_key": "APIcustomer-key",
        "api_secret": "customer-plaintext-secret-32chars",
        "agent_name": "returns-agent",
    }
    body.update(overrides)
    return body


def _fake_livekit_module():
    """A stand-in for ``livekit.api`` whose client records what it was asked."""
    client = MagicMock()
    client.room.create_room = AsyncMock()
    client.room.delete_room = AsyncMock()
    client.agent_dispatch.create_dispatch = AsyncMock()
    client.aclose = AsyncMock()
    return SimpleNamespace(
        LiveKitAPI=MagicMock(return_value=client),
        CreateRoomRequest=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
        DeleteRoomRequest=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
        CreateAgentDispatchRequest=MagicMock(
            side_effect=lambda **kw: SimpleNamespace(**kw)
        ),
        client=client,
    )


def _assert_not_verified(response):
    assert response.status_code == 200
    assert response.data["status"] is True
    assert response.data["result"] == {
        "valid": False,
        "verified": False,
        "skipped": True,
        "message": NOT_VERIFIED,
    }


@pytest.mark.parametrize("deployment", HOSTED)
@pytest.mark.parametrize("url", CUSTOMER_URLS)
def test_hosted_validate_sends_nothing_to_a_customer_livekit_url(
    settings, network, deployment, url
):
    settings.CLOUD_DEPLOYMENT = deployment

    response = _post(_body(url))

    assert network.sent == []
    assert network.sessions == []
    assert network.resolved == []
    _assert_not_verified(response)


@pytest.mark.parametrize("deployment", HOSTED)
def test_hosted_validate_never_builds_the_livekit_client(settings, network, deployment):
    settings.CLOUD_DEPLOYMENT = deployment
    fake = _fake_livekit_module()

    with patch("tfc.utils.lazy_extras.load_extra", return_value=fake) as load:
        response = _post(_body(METADATA_URL))

    load.assert_not_called()
    fake.LiveKitAPI.assert_not_called()
    fake.client.room.create_room.assert_not_awaited()
    fake.client.agent_dispatch.create_dispatch.assert_not_awaited()
    assert network.sent == []
    _assert_not_verified(response)


def test_hosted_validate_does_not_pull_stored_credentials_into_a_probe(
    settings, network
):
    """Masked values plus an agent id used to be rehydrated from the database
    (with no organization filter) and sent to the URL in the request."""
    settings.CLOUD_DEPLOYMENT = "US"
    agents = MagicMock()

    with patch("simulate.views.livekit_api.AgentDefinition.objects", agents):
        response = _post(
            _body(
                METADATA_URL,
                api_key="abcd...wxyz",
                api_secret="********",
                agent_definition_id=str(uuid.uuid4()),
            )
        )

    assert network.sent == []
    assert agents.mock_calls == []
    assert network.sessions == []
    _assert_not_verified(response)


def test_hosted_validate_still_rejects_an_incomplete_form(settings, network):
    settings.CLOUD_DEPLOYMENT = "US"

    response = _post(_body(METADATA_URL, api_secret=""))

    assert response.status_code == 400
    assert network.sent == []


@pytest.mark.parametrize("deployment", HOSTED)
def test_hosted_saving_livekit_credentials_needs_no_verification(
    settings, network, deployment
):
    """The create and edit requests carry LiveKit credentials with no verified
    flag and validate without contacting the URL, so a skipped check never
    blocks saving them."""
    settings.CLOUD_DEPLOYMENT = deployment
    livekit = {
        "provider": "livekit",
        "livekit_url": METADATA_URL,
        "livekit_api_key": "APIcustomer-key",
        "livekit_api_secret": "customer-plaintext-secret-32chars",
        "livekit_agent_name": "returns-agent",
    }

    create = AgentDefinitionCreateRequestSerializer(
        data={
            "agent_name": "Returns agent",
            "agent_type": "voice",
            "commit_message": "First version",
            "languages": ["en"],
            **livekit,
        }
    )
    edit = AgentDefinitionEditRequestSerializer(
        data={"commit_message": "Rotate the LiveKit key", **livekit}, partial=True
    )

    assert create.is_valid(), create.errors
    assert edit.is_valid(), edit.errors
    assert create.validated_data["livekit_url"] == METADATA_URL
    assert edit.validated_data["livekit_api_secret"] == livekit["livekit_api_secret"]
    assert network.sessions == []
    assert network.sent == []


@pytest.mark.parametrize("deployment", SELF_HOSTED)
def test_self_hosted_validate_still_probes_its_livekit_server(settings, deployment):
    settings.CLOUD_DEPLOYMENT = deployment
    fake = _fake_livekit_module()

    with patch("tfc.utils.lazy_extras.load_extra", return_value=fake) as load:
        response = _post(_body("ws://10.0.0.5:7880"))

    load.assert_called_once_with("livekit.api", "voice")
    fake.LiveKitAPI.assert_called_once_with(
        url="http://10.0.0.5:7880",
        api_key="APIcustomer-key",
        api_secret="customer-plaintext-secret-32chars",
    )
    fake.client.room.create_room.assert_awaited_once()
    (dispatch,) = fake.client.agent_dispatch.create_dispatch.await_args.args
    assert dispatch.agent_name == "returns-agent"
    fake.client.room.delete_room.assert_awaited_once()
    fake.client.aclose.assert_awaited_once()
    assert response.status_code == 200
    assert response.data["result"] == {"valid": True}


def test_self_hosted_validate_still_reports_a_rejected_key(settings):
    settings.CLOUD_DEPLOYMENT = ""
    fake = _fake_livekit_module()
    fake.client.room.create_room.side_effect = RuntimeError("401 unauthorized")

    with patch("tfc.utils.lazy_extras.load_extra", return_value=fake):
        response = _post(_body("https://livekit.internal.example"))

    assert response.status_code == 200
    assert response.data["result"] == {"valid": False, "error": "401 unauthorized"}
    fake.client.aclose.assert_awaited_once()


def test_validate_contract_documents_the_not_verified_answer():
    swagger = Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    result = json.loads(swagger.read_text())["definitions"][
        "ValidateLiveKitCredentialsResult"
    ]

    assert result["required"] == ["valid"]
    assert result["properties"]["verified"]["type"] == "boolean"
    assert result["properties"]["skipped"]["type"] == "boolean"
    assert result["properties"]["message"]["type"] == "string"

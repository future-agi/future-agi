"""A connector's address is checked when it is saved.

Creating a connector, or changing its address, checks the address the way a
call to it is checked: an address this deployment does not call is refused
with one sentence, the same for every reason, which names neither the
address nor the host, and nothing is saved. A connector whose address
changes keeps no stored credential, sign-in or sign-in addresses; a
credential sent with the change is the one kept. Finding out whether a
connector signs in with OAuth reads the status of its answer only.

Every request here is made by an organization owner, straight to the view.
No outside network: the fixtures of
test_connector_calls_through_the_address_check answer name lookups from a
table and make loopback connections only.
"""

import json
import secrets
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.falcon_ai import views_connectors
from ee.falcon_ai.credentials import decrypt_falcon_token, encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_connector_calls_through_the_address_check as calls
from tfc.utils.tests.loopback_endpoints import LoopbackServer

pytestmark = pytest.mark.django_db

_environment = calls._environment
persistent_test_key = calls.persistent_test_key
hosted = calls.hosted
self_hosted = calls.self_hosted
names = calls.names
connects = calls.connects
unchecked = calls.unchecked

REFUSED_SENTENCE = "This server address can't be used for a connector."
PUBLIC_IP = calls.PUBLIC_IP
TOOLS_HOST = calls.TOOLS_HOST
STORED_URL = f"https://{TOOLS_HOST}/mcp"

# (address, lookup answers, whether the host is looked up at all)
REFUSED_AT_SAVE = [
    pytest.param("https://10.0.0.7/mcp", {}, False, id="private-address"),
    pytest.param("https://127.0.0.1/mcp", {}, False, id="loopback-address"),
    pytest.param("https://169.254.10.10/mcp", {}, False, id="link-local"),
    pytest.param("https://[fd00::12]/mcp", {}, False, id="ipv6-unique-local"),
    pytest.param(
        "https://notes.example.com/mcp",
        {"notes.example.com": ["192.168.7.7"]},
        True,
        id="name-with-a-private-answer",
    ),
    pytest.param(
        "https://mixed.example.com/mcp",
        {"mixed.example.com": [PUBLIC_IP, "10.1.2.3"]},
        True,
        id="name-with-one-public-and-one-private-answer",
    ),
    pytest.param("https://unknown.example.com/mcp", {}, True, id="name-with-no-answer"),
    pytest.param(
        "https://localhost/mcp", {"localhost": [PUBLIC_IP]}, False, id="localhost"
    ),
    pytest.param(
        "https://reports.default.svc.cluster.local/mcp",
        {"reports.default.svc.cluster.local": [PUBLIC_IP]},
        False,
        id="name-no-public-server-has",
    ),
    pytest.param(
        f"http://{TOOLS_HOST}/mcp",
        {TOOLS_HOST: [PUBLIC_IP]},
        False,
        id="plain-http-on-future-agi-cloud",
    ),
]

SIGN_IN_FIELDS = (
    "auth_header_value",
    "oauth_client_id",
    "oauth_client_secret",
    "oauth_access_token",
    "oauth_refresh_token",
    "oauth_code_verifier",
    "oauth_state",
)


def check(condition, message):
    # Only the message is shown on a failure, never the compared values.
    assert condition, message


def send(view, method, user, workspace, data=None, record=None):
    request = getattr(APIRequestFactory(), method)(
        "/connector/", data or {}, format="json"
    )
    force_authenticate(request, user=user)
    request.organization = user.organization
    request.workspace = workspace
    kwargs = {"connector_id": record.pk} if record else {}
    return view.as_view()(request, **kwargs)


def create(user, workspace, data):
    return send(views_connectors.MCPConnectorListView, "post", user, workspace, data)


def change(user, record, data):
    view = views_connectors.MCPConnectorDetailView
    return send(view, "patch", user, record.workspace, data, record)


def authenticate(user, record):
    view = views_connectors.MCPConnectorAuthenticateView
    return send(view, "post", user, record.workspace, record=record)


def row(record):
    return MCPConnector._base_manager.get(pk=record.pk)


def saved_connector(user, workspace, url=STORED_URL, **fields):
    return MCPConnector.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        name="tools-" + secrets.token_hex(6),
        server_url=url,
        **fields,
    )


def signed_in_connector(user, workspace):
    """A connector with a stored credential, an OAuth sign-in and the sign-in
    addresses its server advertised."""
    return saved_connector(
        user,
        workspace,
        auth_type="oauth",
        auth_header_value=encrypt_falcon_token("key-value"),
        oauth_client_id="registered-client",
        oauth_client_secret=encrypt_falcon_token("client-value"),
        oauth_access_token=encrypt_falcon_token("access-value"),
        oauth_refresh_token=encrypt_falcon_token("refresh-value"),
        oauth_code_verifier=secrets.token_urlsafe(48),
        oauth_state=secrets.token_urlsafe(16),
        oauth_token_expires_at=timezone.now(),
        oauth_server_metadata={
            "authorization_endpoint": f"https://{TOOLS_HOST}/authorize",
            "token_endpoint": f"https://{TOOLS_HOST}/token",
        },
        is_verified=True,
    )


def mentions(data, url):
    shown = json.dumps(data, default=str)
    return url in shown or (urlsplit(url).hostname or "") in shown


# A connector's address is checked when it is created.


@pytest.mark.parametrize(("url", "answers", "looked_up"), REFUSED_AT_SAVE)
def test_a_connector_at_an_address_this_deployment_refuses_is_not_created(
    url, answers, looked_up, hosted, names, connects, user, workspace
):
    calls.add_answers(names, answers)

    response = create(user, workspace, {"name": "Reports tools", "server_url": url})

    assert response.status_code == 400
    assert response.data["detail"] == REFUSED_SENTENCE
    assert not mentions(response.data, url)
    assert not MCPConnector.no_workspace_objects.filter(
        workspace=workspace, name="Reports tools"
    ).exists()
    assert bool(names.lookups) is looked_up
    assert connects == []


def test_a_refused_address_is_logged_by_the_reason_class_only(
    hosted, names, user, workspace
):
    names.add("notes.example.com", "192.168.7.7")
    data = {"name": "Reports tools", "server_url": "https://notes.example.com/mcp"}

    with patch("ee.falcon_ai.views_connectors.logger") as logger:
        response = create(user, workspace, data)

    logged = str(logger.mock_calls)
    assert response.status_code == 400
    assert "notes.example.com" not in logged
    assert "192.168.7.7" not in logged
    assert "EndpointBlocked" in logged


def test_a_connector_at_a_public_address_is_created_after_its_host_is_checked(
    hosted, names, connects, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    data = {"name": "Reports tools", "server_url": STORED_URL}

    response = create(user, workspace, data)

    assert response.status_code == 201
    assert names.lookups == [TOOLS_HOST]
    assert connects == []
    assert MCPConnector.no_workspace_objects.filter(
        workspace=workspace, name="Reports tools", server_url=STORED_URL
    ).exists()


def test_a_self_hosted_install_creates_a_connector_on_its_own_network(
    self_hosted, names, connects, user, workspace
):
    names.add("reports.internal", "10.0.0.7")
    url = "http://reports.internal:8080/mcp"

    response = create(user, workspace, {"name": "Reports tools", "server_url": url})

    assert response.status_code == 201
    assert names.lookups == ["reports.internal"]
    assert connects == []
    assert MCPConnector.no_workspace_objects.filter(
        workspace=workspace, server_url=url
    ).exists()


# A connector's address is checked when it changes.


@pytest.mark.parametrize(("url", "answers", "looked_up"), REFUSED_AT_SAVE)
def test_changing_a_connector_to_a_refused_address_saves_nothing(
    url, answers, looked_up, hosted, names, connects, user, workspace
):
    calls.add_answers(names, answers)
    record = saved_connector(user, workspace)

    response = change(user, record, {"name": "Renamed", "server_url": url})

    assert response.status_code == 400
    assert response.data["detail"] == REFUSED_SENTENCE
    assert not mentions(response.data, url)
    stored = row(record)
    assert stored.server_url == STORED_URL
    assert stored.name == record.name
    assert bool(names.lookups) is looked_up
    assert connects == []


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "Renamed"}, id="name-only"),
        pytest.param({"server_url": STORED_URL}, id="same-address"),
        pytest.param(
            {"name": "Renamed", "server_url": STORED_URL}, id="name-and-same-address"
        ),
    ],
)
def test_a_change_that_keeps_the_address_does_not_look_it_up(
    data, hosted, names, connects, user, workspace
):
    record = saved_connector(user, workspace)

    response = change(user, record, data)

    assert response.status_code == 200
    assert names.lookups == []
    assert row(record).server_url == STORED_URL


# A connector whose address changes starts without the stored sign-in.


@pytest.mark.parametrize(
    "new_url",
    [
        pytest.param("https://other.example.org/mcp", id="new-host"),
        pytest.param(f"https://{TOOLS_HOST}:8443/mcp", id="new-port"),
        pytest.param(f"http://{TOOLS_HOST}/mcp", id="new-scheme"),
    ],
)
def test_moving_a_connector_to_another_origin_clears_its_stored_sign_in(
    new_url, self_hosted, names, connects, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    names.add("other.example.org", PUBLIC_IP)
    record = signed_in_connector(user, workspace)

    response = change(user, record, {"server_url": new_url})

    assert response.status_code == 200
    stored = row(record)
    assert stored.server_url == new_url
    for field in SIGN_IN_FIELDS:
        check(getattr(stored, field) == "", f"{field} was kept")
    assert stored.oauth_server_metadata == {}
    assert stored.oauth_token_expires_at is None
    assert stored.is_verified is False


@pytest.mark.parametrize(
    "new_url",
    [
        pytest.param(f"https://{TOOLS_HOST}/v2/mcp", id="new-path"),
        pytest.param(f"https://{TOOLS_HOST}:443/mcp/", id="default-port-written-out"),
    ],
)
def test_a_new_path_on_the_same_origin_clears_the_stored_sign_in(
    new_url, self_hosted, names, connects, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    record = signed_in_connector(user, workspace)

    response = change(user, record, {"server_url": new_url})

    assert response.status_code == 200
    stored = row(record)
    assert stored.server_url == new_url
    for field in SIGN_IN_FIELDS:
        check(getattr(stored, field) == "", f"{field} was kept")
    assert stored.oauth_server_metadata == {}
    assert stored.oauth_token_expires_at is None
    assert stored.is_verified is False


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "Renamed"}, id="name-only"),
        pytest.param({"server_url": STORED_URL}, id="same-address"),
    ],
)
def test_a_change_that_keeps_the_address_keeps_the_stored_sign_in(
    data, self_hosted, names, connects, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    record = signed_in_connector(user, workspace)
    before = row(record)

    response = change(user, record, data)

    assert response.status_code == 200
    stored = row(record)
    for field in SIGN_IN_FIELDS:
        check(getattr(stored, field) == getattr(before, field), f"{field} changed")
    assert stored.oauth_server_metadata == before.oauth_server_metadata
    assert stored.oauth_token_expires_at == before.oauth_token_expires_at
    assert stored.is_verified is True


def test_a_credential_sent_with_the_new_origin_is_the_one_kept(
    self_hosted, names, connects, user, workspace
):
    names.add("other.example.org", PUBLIC_IP)
    record = signed_in_connector(user, workspace)
    data = {
        "server_url": "https://other.example.org/mcp",
        "auth_header_value": "new-key-value",
    }

    response = change(user, record, data)

    assert response.status_code == 200
    stored = row(record)
    check(
        decrypt_falcon_token(stored.auth_header_value) == "new-key-value",
        "the new credential was not kept",
    )
    for field in SIGN_IN_FIELDS[1:]:
        check(getattr(stored, field) == "", f"{field} was kept")
    assert stored.oauth_server_metadata == {}


def test_a_credential_sent_with_a_new_path_is_the_one_kept(
    self_hosted, names, connects, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    record = signed_in_connector(user, workspace)
    data = {
        "server_url": f"https://{TOOLS_HOST}/v2/mcp",
        "auth_header_value": "new-key-value",
    }

    response = change(user, record, data)

    assert response.status_code == 200
    stored = row(record)
    check(
        decrypt_falcon_token(stored.auth_header_value) == "new-key-value",
        "the new credential was not kept",
    )
    for field in SIGN_IN_FIELDS[1:]:
        check(getattr(stored, field) == "", f"{field} was kept")
    assert stored.oauth_server_metadata == {}


# Finding out whether a connector signs in with OAuth reads a status only.


@pytest.fixture
def body_reads(monkeypatch):
    """The method of every request whose answer body is read."""
    read = []
    real_read = httpx.Response.read

    def recording_read(self):
        read.append(self.request.method)
        return real_read(self)

    monkeypatch.setattr(httpx.Response, "read", recording_read)
    return read


def probe_answer(request):
    if request.method == "GET":
        stream = b"event: message\ndata: probe\n\n"
        return 200, {"Content-Type": "text/event-stream"}, stream
    return calls.tool_answer(request)


def test_finding_out_how_a_connector_signs_in_does_not_read_the_answer_body(
    self_hosted, names, connects, body_reads, user, workspace
):
    with LoopbackServer(probe_answer) as server:
        names.add(TOOLS_HOST, "127.0.0.1")
        record = saved_connector(
            user, workspace, f"http://{TOOLS_HOST}:{server.port}/mcp"
        )
        response = authenticate(user, record)
        methods = [item.method for item in server.requests]

    assert "GET" not in body_reads
    assert methods[0] == "GET"
    assert response.status_code == 200
    assert response.data["message"] == "Connected successfully"
    assert row(record).auth_type == "none"


def test_finding_out_how_a_connector_signs_in_does_not_follow_a_redirect(
    self_hosted, names, connects, user, workspace
):
    with LoopbackServer(calls.tool_answer) as target:

        def moved(request):
            if request.method == "GET":
                return 302, {"Location": f"{target.base}/mcp"}, b""
            return calls.tool_answer(request)

        with LoopbackServer(moved) as server:
            names.add(TOOLS_HOST, "127.0.0.1")
            record = saved_connector(
                user, workspace, f"http://{TOOLS_HOST}:{server.port}/mcp"
            )
            response = authenticate(user, record)
        reached = list(target.requests)

    assert reached == []
    assert response.status_code == 200
    assert row(record).auth_type == "none"


def test_finding_out_how_a_connector_signs_in_opens_no_connection_to_a_refused_address(
    hosted, names, connects, unchecked, user, workspace
):
    names.add(TOOLS_HOST, "10.0.0.7")
    record = saved_connector(user, workspace)

    response = authenticate(user, record)

    assert unchecked == []
    assert connects == []
    assert response.status_code == 200
    assert response.data["status"] is False
    assert response.data["message"] == "MCP request failed"
    stored = row(record)
    assert stored.last_error == "Connection failed"
    assert stored.auth_type == "none"


def test_a_connector_that_answers_401_is_set_to_sign_in_with_oauth(
    self_hosted, names, connects, user, workspace
):
    with LoopbackServer(lambda request: (401, {}, b"")) as server:
        names.add(TOOLS_HOST, "127.0.0.1")
        record = saved_connector(
            user, workspace, f"http://{TOOLS_HOST}:{server.port}/mcp"
        )
        response = authenticate(user, record)

    assert row(record).auth_type == "oauth"
    assert response.status_code == 502
    assert response.data["detail"] == "OAuth setup failed"

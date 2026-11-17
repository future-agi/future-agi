"""An owner or admin gives a connector the OAuth client its server issued.

The client ID and secret are entered when a connector is added or changed.
Sign-in then uses that client instead of registering one. The secret is stored
encrypted, is never shown again (the connector says only that one is saved), a
blank or missing secret on a change keeps the saved one, and ``null`` removes
it. A new client ID drops the old client's secret and sign-in, and a connector
moved to another address keeps no client.

Requests go straight to the view, the way the owner and admin tests send them.
Only made-up values are used, and a failure message never shows a value.
"""

import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from django.utils import timezone

from ee.falcon_ai import oauth_client
from ee.falcon_ai.credentials import decrypt_falcon_token, encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.serializers_connectors import MCPConnectorCreateSerializer
from ee.falcon_ai.serializers_contracts import MCPConnectorUpdateRequestSerializer
from ee.falcon_ai.tests import test_connector_calls_through_the_address_check as calls
from ee.falcon_ai.tests import test_connector_management_by_owners_and_admins as base

pytestmark = pytest.mark.django_db

_environment = calls._environment
persistent_test_key = calls.persistent_test_key
self_hosted = calls.self_hosted
names = calls.names
connects = calls.connects
unchecked = calls.unchecked

check = base.check
give_role = base.give_role
send = base.send
row = base.row
snapshot = base.snapshot
add_hosts = base.add_hosts
LIST = base.LIST
DETAIL = base.DETAIL
SIGN_IN = base.SIGN_IN
NEW_URL = base.NEW_URL
STORED_URL = base.STORED_URL
ADD_SENTENCE = base.ADD_SENTENCE
CHANGE_SENTENCE = base.CHANGE_SENTENCE

CLIENT_ID = "client-id-example"
CLIENT_SECRET = "client-secret-example"
NEW_CLIENT_ID = "other-client-id-example"
NEW_CLIENT_SECRET = "other-client-secret-example"
ACCESS_VALUE = "access-token-example"
REFRESH_VALUE = "refresh-token-example"
ADDED = {
    "name": "Reports tools",
    "server_url": STORED_URL,
    "auth_type": "oauth",
    "oauth_client_id": CLIENT_ID,
    "oauth_client_secret": CLIENT_SECRET,
}
METADATA = {
    "authorization_endpoint": f"https://{calls.TOOLS_HOST}/authorize",
    "token_endpoint": f"https://{calls.TOOLS_HOST}/token",
}
OWNER_OR_ADMIN = ["owner", "admin"]


def with_own_client(workspace, **fields):
    """A signed-in OAuth connector that has its own client saved."""
    return base.saved_connector(
        workspace,
        STORED_URL,
        auth_type="oauth",
        oauth_client_id=CLIENT_ID,
        oauth_client_secret=encrypt_falcon_token(CLIENT_SECRET),
        oauth_access_token=encrypt_falcon_token(ACCESS_VALUE),
        oauth_refresh_token=encrypt_falcon_token(REFRESH_VALUE),
        oauth_token_expires_at=timezone.now(),
        oauth_server_metadata=METADATA,
        is_verified=True,
        **fields,
    )


def added_row(workspace):
    return MCPConnector._base_manager.get(
        workspace=workspace, name=ADDED["name"], deleted=False
    )


def secret_of(stored):
    return decrypt_falcon_token(stored.oauth_client_secret)


# Adding a connector with its own client.


@pytest.mark.parametrize("role", OWNER_OR_ADMIN)
def test_adding_a_connector_stores_the_client_id_and_the_secret_encrypted(
    role, self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, role)

    response = send(LIST, "post", user, workspace, ADDED)

    status_code = response.status_code
    assert status_code == 201
    stored = added_row(workspace)
    check(stored.oauth_client_id == CLIENT_ID, "the client ID was not stored")
    cipher = stored.oauth_client_secret
    check(cipher.startswith("enc::"), "the secret is not encrypted")
    check(CLIENT_SECRET not in cipher, "the secret is stored as typed")
    check(secret_of(stored) == CLIENT_SECRET, "the secret does not read back")


@pytest.mark.parametrize("role", OWNER_OR_ADMIN)
def test_the_answer_names_the_client_id_and_never_the_secret(
    role, self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, role)

    created = send(LIST, "post", user, workspace, ADDED)
    record = added_row(workspace)
    read = send(DETAIL, "get", user, workspace, record=record)
    listed = send(LIST, "get", user, workspace)

    status_codes = (created.status_code, read.status_code, listed.status_code)
    assert status_codes == (201, 200, 200)
    for answer in (created, read):
        result = answer.data["result"]
        check(result["oauth_client_id"] == CLIENT_ID, "the client ID is not named")
        check(result["has_oauth_client_secret"] is True, "a saved secret not said")
        check("oauth_client_secret" not in result, "the secret field is returned")
    shown = json.dumps([created.data, read.data, listed.data], default=str)
    check(CLIENT_SECRET not in shown, "the secret was shown")
    check(record.oauth_client_secret not in shown, "the stored secret was shown")


def test_a_client_id_without_a_secret_is_a_public_client(
    self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, "owner")
    data = {k: v for k, v in ADDED.items() if k != "oauth_client_secret"}

    response = send(LIST, "post", user, workspace, data)

    status_code = response.status_code
    assert status_code == 201
    result = response.data["result"]
    check(result["oauth_client_id"] == CLIENT_ID, "the client ID is not named")
    check(result["has_oauth_client_secret"] is False, "a secret is said to be saved")
    check(added_row(workspace).oauth_client_secret == "", "a secret was stored")


def test_a_secret_without_a_client_id_is_refused_when_adding(
    self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, "owner")
    data = {k: v for k, v in ADDED.items() if k != "oauth_client_id"}

    response = send(LIST, "post", user, workspace, data)

    status_code = response.status_code
    assert status_code == 400
    check(
        not MCPConnector._base_manager.filter(workspace=workspace).exists(),
        "a connector was added",
    )


@pytest.mark.parametrize(
    "serializer", [MCPConnectorCreateSerializer, MCPConnectorUpdateRequestSerializer]
)
def test_the_secret_input_is_write_only(serializer):
    check(serializer().fields["oauth_client_secret"].write_only, "secret readable")


# Changing the client of a connector.


@pytest.mark.parametrize("role", OWNER_OR_ADMIN)
@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "Renamed"}, id="secret-missing"),
        pytest.param({"oauth_client_secret": ""}, id="secret-blank"),
        pytest.param({"oauth_client_id": CLIENT_ID}, id="same-client-id"),
        pytest.param(
            {"oauth_client_id": CLIENT_ID, "oauth_client_secret": ""},
            id="same-client-id-and-blank-secret",
        ),
    ],
)
def test_a_blank_or_missing_secret_keeps_the_saved_one(
    data, role, self_hosted, names, connects, user, workspace
):
    record = with_own_client(workspace)
    give_role(user, workspace.organization, role)

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.oauth_client_id == CLIENT_ID, "the client ID changed")
    check(secret_of(stored) == CLIENT_SECRET, "the saved secret was not kept")
    check(
        decrypt_falcon_token(stored.oauth_access_token) == ACCESS_VALUE,
        "the sign-in was dropped",
    )
    check(stored.is_verified is True, "the connector is no longer verified")
    check(response.data["result"]["has_oauth_client_secret"] is True, "not said")


@pytest.mark.parametrize("role", OWNER_OR_ADMIN)
def test_a_new_secret_replaces_the_saved_one(
    role, self_hosted, names, connects, user, workspace
):
    record = with_own_client(workspace)
    give_role(user, workspace.organization, role)

    response = send(
        DETAIL,
        "patch",
        user,
        workspace,
        {"oauth_client_secret": NEW_CLIENT_SECRET},
        record,
    )

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.oauth_client_id == CLIENT_ID, "the client ID changed")
    check(stored.oauth_client_secret.startswith("enc::"), "secret not encrypted")
    check(secret_of(stored) == NEW_CLIENT_SECRET, "the new secret was not kept")
    shown = json.dumps(response.data, default=str)
    check(NEW_CLIENT_SECRET not in shown, "the new secret was shown")


@pytest.mark.parametrize("role", OWNER_OR_ADMIN)
def test_a_null_secret_removes_the_saved_one(
    role, self_hosted, names, connects, user, workspace
):
    record = with_own_client(workspace)
    give_role(user, workspace.organization, role)

    response = send(
        DETAIL, "patch", user, workspace, {"oauth_client_secret": None}, record
    )

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.oauth_client_secret == "", "the secret was kept")
    check(stored.oauth_client_id == CLIENT_ID, "the client ID changed")
    check(response.data["result"]["has_oauth_client_secret"] is False, "still saved")


@pytest.mark.parametrize("with_new_secret", [False, True])
def test_a_new_client_id_drops_the_old_secret_and_sign_in(
    with_new_secret, self_hosted, names, connects, user, workspace
):
    record = with_own_client(workspace)
    give_role(user, workspace.organization, "owner")
    data = {"oauth_client_id": NEW_CLIENT_ID}
    if with_new_secret:
        data["oauth_client_secret"] = NEW_CLIENT_SECRET

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.oauth_client_id == NEW_CLIENT_ID, "the client ID was not changed")
    expected = NEW_CLIENT_SECRET if with_new_secret else ""
    check(secret_of(stored) == expected, "the secret is not the new client's")
    check(stored.oauth_access_token == "", "the old access token was kept")
    check(stored.oauth_refresh_token == "", "the old refresh token was kept")
    check(stored.is_verified is False, "the connector is still verified")
    check(stored.oauth_server_metadata == METADATA, "the server's addresses went")


def test_a_secret_without_a_client_id_is_refused_when_changing(
    self_hosted, names, connects, user, workspace
):
    record = base.saved_connector(workspace, STORED_URL, auth_type="oauth")
    give_role(user, workspace.organization, "owner")
    before = snapshot(record)

    response = send(
        DETAIL,
        "patch",
        user,
        workspace,
        {"oauth_client_secret": CLIENT_SECRET},
        record,
    )

    status_code = response.status_code
    assert status_code == 400
    check(snapshot(record) == before, "the connector changed")


def test_clearing_the_client_id_clears_its_secret(
    self_hosted, names, connects, user, workspace
):
    record = with_own_client(workspace)
    give_role(user, workspace.organization, "owner")

    response = send(DETAIL, "patch", user, workspace, {"oauth_client_id": ""}, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.oauth_client_id == "", "the client ID was kept")
    check(stored.oauth_client_secret == "", "the secret was kept")


@pytest.mark.parametrize("role", ["member", "viewer"])
@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"oauth_client_id": NEW_CLIENT_ID}, id="client-id"),
        pytest.param({"oauth_client_secret": NEW_CLIENT_SECRET}, id="secret"),
        pytest.param({"oauth_client_secret": None}, id="remove-secret"),
    ],
)
def test_a_member_or_a_viewer_is_told_who_can_change_the_client(
    data, role, self_hosted, names, connects, unchecked, user, workspace
):
    add_hosts(names)
    record = with_own_client(workspace)
    before = snapshot(record)
    give_role(user, workspace.organization, role)

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == CHANGE_SENTENCE, "not told who can change it")
    check(snapshot(record) == before, "the connector changed")


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_member_or_a_viewer_cannot_add_a_connector_with_a_client(
    role, self_hosted, names, connects, unchecked, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, role)

    response = send(LIST, "post", user, workspace, ADDED)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == ADD_SENTENCE, "not told who can add one")
    check(
        not MCPConnector._base_manager.filter(workspace=workspace).exists(),
        "a connector was added",
    )


# Moving a connector to another address.


def test_moving_a_connector_clears_its_client_id_and_secret(
    self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    record = with_own_client(workspace)
    give_role(user, workspace.organization, "owner")

    response = send(DETAIL, "patch", user, workspace, {"server_url": NEW_URL}, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.server_url == NEW_URL, "the address was not changed")
    check(stored.oauth_client_id == "", "the client ID was kept")
    check(stored.oauth_client_secret == "", "the client secret was kept")
    check(response.data["result"]["has_oauth_client_secret"] is False, "still saved")


def test_a_client_sent_with_the_move_is_the_one_kept(
    self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    record = with_own_client(workspace)
    give_role(user, workspace.organization, "owner")
    data = {
        "server_url": NEW_URL,
        "oauth_client_id": CLIENT_ID,
        "oauth_client_secret": NEW_CLIENT_SECRET,
    }

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.server_url == NEW_URL, "the address was not changed")
    check(stored.oauth_client_id == CLIENT_ID, "the client ID sent was dropped")
    check(secret_of(stored) == NEW_CLIENT_SECRET, "the secret sent was dropped")
    check(stored.oauth_access_token == "", "the old sign-in was kept")


# Signing in with the saved client.


def test_sign_in_uses_the_saved_client_and_does_not_register_one(
    self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, "owner")
    send(LIST, "post", user, workspace, ADDED)
    record = added_row(workspace)
    MCPConnector._base_manager.filter(pk=record.pk).update(
        oauth_server_metadata=METADATA
    )
    record = row(record)

    with patch.object(oauth_client, "register_client") as register:
        response = send(SIGN_IN, "post", user, workspace, record=record)

    status_code = response.status_code
    assert status_code == 200
    check(not register.called, "a client was registered")
    params = parse_qs(urlparse(response.data["authorization_url"]).query)
    check(params["client_id"] == [CLIENT_ID], "the saved client ID was not used")

    stored = row(record)
    check(stored.oauth_client_id == CLIENT_ID, "the client ID was replaced")
    check(secret_of(stored) == CLIENT_SECRET, "the saved secret was replaced")
    token_response = Mock()
    token_response.json.return_value = {"access_token": ACCESS_VALUE}
    with patch.object(httpx.Client, "post", return_value=token_response) as post:
        oauth_client.exchange_code_for_tokens(
            stored, "test-code", "https://app.example.invalid/cb"
        )
    sent = post.call_args.kwargs["data"]
    check(sent["client_id"] == CLIENT_ID, "the token step used another client")
    check(sent.get("client_secret") == CLIENT_SECRET, "the saved secret not sent")

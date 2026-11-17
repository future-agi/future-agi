"""The credential header name a person chooses for a connector.

A connector that signs in with a key sends the key under the header name the
person chooses; a bearer or signed-in connector sends Authorization. Creating
a connector, or changing its credential header name, refuses a name the
platform's address check drops from the calls it makes, with one sentence,
and saves nothing. Authorization and X-Api-Key are accepted. A connector
stored with such a name keeps it when other fields change.

Every request here is made by an organization owner, straight to the view. No
outside network: the fixtures of test_connector_calls_through_the_address_check
answer name lookups from a table and make loopback connections only.
"""

import secrets

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.falcon_ai import views_connectors
from ee.falcon_ai.credentials import encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_connector_calls_through_the_address_check as calls

pytestmark = pytest.mark.django_db

_environment = calls._environment
persistent_test_key = calls.persistent_test_key
hosted = calls.hosted
names = calls.names
connects = calls.connects

HEADER_NAME_REFUSED = (
    "This header name can't be used for a connector's credential. "
    "Choose another header name."
)
STORED_URL = f"https://{calls.TOOLS_HOST}/mcp"

# Header names the address check drops from a connector call.
DROPPED = [
    pytest.param("Proxy-Authorization", id="proxy-prefix"),
    pytest.param("X-Forwarded-For", id="forwarding-prefix"),
    pytest.param("X-Real-IP", id="forwarding-name"),
]
CARRIED = [
    pytest.param("Authorization", id="authorization"),
    pytest.param("X-Api-Key", id="api-key"),
]


def send(view, method, user, workspace, data, record=None):
    request = getattr(APIRequestFactory(), method)("/connector/", data, format="json")
    force_authenticate(request, user=user)
    request.organization = user.organization
    request.workspace = workspace
    kwargs = {"connector_id": record.pk} if record else {}
    return view.as_view()(request, **kwargs)


def create(user, workspace, header_name):
    data = {
        "name": "Reports tools",
        "server_url": STORED_URL,
        "auth_type": "api_key",
        "auth_header_name": header_name,
        "auth_header_value": "key-value",
    }
    return send(views_connectors.MCPConnectorListView, "post", user, workspace, data)


def change(user, record, data):
    view = views_connectors.MCPConnectorDetailView
    return send(view, "patch", user, record.workspace, data, record)


def stored_connector(user, workspace, header_name):
    return MCPConnector.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        name="tools-" + secrets.token_hex(6),
        server_url=STORED_URL,
        auth_type="api_key",
        auth_header_name=header_name,
        auth_header_value=encrypt_falcon_token("key-value"),
    )


def row(record):
    return MCPConnector._base_manager.get(pk=record.pk)


def created(workspace, **fields):
    return MCPConnector.no_workspace_objects.filter(
        workspace=workspace, name="Reports tools", **fields
    ).exists()


@pytest.mark.parametrize("header_name", DROPPED)
def test_a_connector_is_not_created_with_a_header_name_its_calls_cannot_carry(
    header_name, hosted, names, connects, user, workspace
):
    names.add(calls.TOOLS_HOST, calls.PUBLIC_IP)

    response = create(user, workspace, header_name)

    assert response.status_code == 400
    assert response.data["detail"] == HEADER_NAME_REFUSED
    assert not created(workspace)
    assert connects == []


@pytest.mark.parametrize("header_name", DROPPED)
def test_changing_to_a_header_name_its_calls_cannot_carry_saves_nothing(
    header_name, hosted, names, connects, user, workspace
):
    record = stored_connector(user, workspace, "X-Api-Key")
    before = row(record)
    data = {"name": "Renamed", "auth_header_name": header_name}

    response = change(user, record, data)

    assert response.status_code == 400
    assert response.data["detail"] == HEADER_NAME_REFUSED
    stored = row(record)
    assert stored.auth_header_name == "X-Api-Key"
    assert stored.name == before.name
    assert stored.credential_generation == before.credential_generation


@pytest.mark.parametrize("header_name", CARRIED)
def test_a_connector_is_created_with_an_authorization_or_key_header(
    header_name, hosted, names, connects, user, workspace
):
    names.add(calls.TOOLS_HOST, calls.PUBLIC_IP)

    response = create(user, workspace, header_name)

    assert response.status_code == 201
    assert created(workspace, auth_header_name=header_name)


@pytest.mark.parametrize("header_name", CARRIED)
def test_a_credential_header_name_changes_to_an_authorization_or_key_header(
    header_name, hosted, names, connects, user, workspace
):
    record = stored_connector(user, workspace, "X-Connector-Key")

    response = change(user, record, {"auth_header_name": header_name})

    assert response.status_code == 200
    assert row(record).auth_header_name == header_name


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "Renamed"}, id="name-only"),
        pytest.param(
            {"name": "Renamed", "auth_header_name": "X-Forwarded-For"},
            id="resends-the-stored-header-name",
        ),
    ],
)
def test_a_connector_stored_with_such_a_name_keeps_it_when_other_fields_change(
    data, hosted, names, connects, user, workspace
):
    record = stored_connector(user, workspace, "X-Forwarded-For")

    response = change(user, record, data)

    assert response.status_code == 200
    stored = row(record)
    assert stored.name == "Renamed"
    assert stored.auth_header_name == "X-Forwarded-For"

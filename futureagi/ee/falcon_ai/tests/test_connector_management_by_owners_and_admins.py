"""Organization owners and admins manage Falcon AI's connectors.

Adding a connector, changing any of its settings (its name too), removing it,
discovering its tools, testing its connection and signing it in are for an
owner or an admin of the organization the request is made in. Anyone else is
told who can, in one sentence, and nothing changes: the connector keeps every
stored field, its credential and its sign-in, no name is looked up and no
connection is opened. Members and viewers list the connectors and read one,
and what they read carries no stored credential or sign-in.

Requests go straight to the view. The organization of the request and the
per-request organization are set from one choice, the way sign-in sets them.
No outside network: the fixtures of
test_connector_calls_through_the_address_check answer name lookups from a
table and make loopback connections only.
"""

import json
import secrets

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from ee.falcon_ai import views_connectors
from ee.falcon_ai.credentials import decrypt_falcon_token, encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_connector_calls_through_the_address_check as calls
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context

pytestmark = pytest.mark.django_db

_environment = calls._environment
persistent_test_key = calls.persistent_test_key
self_hosted = calls.self_hosted
names = calls.names
connects = calls.connects
unchecked = calls.unchecked

ADD_SENTENCE = (
    "Only an organization owner or admin can add a connector. "
    "Ask one of them to add it."
)
CHANGE_SENTENCE = (
    "Only an organization owner or admin can change a connector. "
    "Ask one of them to change it."
)

LIST = views_connectors.MCPConnectorListView
DETAIL = views_connectors.MCPConnectorDetailView
DISCOVER = views_connectors.MCPConnectorDiscoverView
TEST = views_connectors.MCPConnectorTestView
SIGN_IN = views_connectors.MCPConnectorAuthenticateView

STORED_URL = f"https://{calls.TOOLS_HOST}/mcp"
NEW_HOST = "other.example.org"
NEW_URL = f"https://{NEW_HOST}/mcp"
KEY_VALUE = "key-" + secrets.token_hex(8)
CLIENT_VALUE = "client-" + secrets.token_hex(8)
ACCESS_VALUE = "access-" + secrets.token_hex(8)
REFRESH_VALUE = "refresh-" + secrets.token_hex(8)
NEW_VALUE = "new-" + secrets.token_hex(8)
NEW_CONNECTOR = {
    "name": "Reports tools",
    "server_url": STORED_URL,
    "auth_type": "bearer",
    "auth_header_value": NEW_VALUE,
}
SIGN_IN_FIELDS = (
    "auth_header_value",
    "oauth_client_secret",
    "oauth_access_token",
    "oauth_refresh_token",
    "oauth_code_verifier",
    "oauth_state",
)
ROLES = {
    "owner": (OrganizationRoles.OWNER, Level.OWNER),
    "admin": (OrganizationRoles.ADMIN, Level.ADMIN),
    "member": (OrganizationRoles.MEMBER, Level.MEMBER),
    "viewer": (OrganizationRoles.MEMBER_VIEW_ONLY, Level.VIEWER),
}

# Each action on a stored connector: the view, its method and what is sent.
CONNECTOR_ACTIONS = [
    pytest.param(DETAIL, "patch", {"name": "Renamed"}, id="rename"),
    pytest.param(DETAIL, "patch", {"server_url": NEW_URL}, id="new-address"),
    pytest.param(DETAIL, "patch", {"transport": "sse"}, id="new-transport"),
    pytest.param(DETAIL, "patch", {"auth_type": "bearer"}, id="new-sign-in-type"),
    pytest.param(
        DETAIL,
        "patch",
        {"auth_header_name": "X-Api-Key"},
        id="new-header-name",
    ),
    pytest.param(
        DETAIL,
        "patch",
        {"auth_header_value": NEW_VALUE},
        id="new-credential",
    ),
    pytest.param(DETAIL, "patch", {"is_active": False}, id="turn-off"),
    pytest.param(DETAIL, "delete", None, id="remove"),
    pytest.param(DISCOVER, "post", None, id="discover"),
    pytest.param(TEST, "post", None, id="test"),
    pytest.param(SIGN_IN, "post", None, id="sign-in"),
]
ADD_ACTION = [pytest.param(LIST, "post", NEW_CONNECTOR, id="add")]

# Settings an owner or an admin changes without moving the connector.
CHANGES = [
    pytest.param({"name": "Renamed"}, id="rename"),
    pytest.param({"transport": "sse"}, id="new-transport"),
    pytest.param({"auth_type": "bearer"}, id="new-sign-in-type"),
    pytest.param({"auth_header_name": "X-Api-Key"}, id="new-header-name"),
    pytest.param({"is_active": False}, id="turn-off"),
]

# The checks an owner or an admin runs against a connector that answers.
CHECKS = [
    pytest.param(DISCOVER, id="discover"),
    pytest.param(TEST, id="test"),
    pytest.param(SIGN_IN, id="sign-in"),
]

ADDRESS_OR_CREDENTIAL = [
    pytest.param({"server_url": NEW_URL}, id="new-address"),
    pytest.param({"auth_header_value": NEW_VALUE}, id="new-credential"),
    pytest.param(
        {"server_url": NEW_URL, "auth_header_value": NEW_VALUE},
        id="new-address-and-credential",
    ),
]


def check(condition, message):
    # Only the message is shown on a failure, never the compared values.
    assert condition, message


def give_role(user, organization, role):
    """Make the person an owner, admin, member or viewer of ``organization``."""
    name, level = ROLES[role]
    memberships = OrganizationMembership._base_manager.filter(user=user)
    rows = memberships.filter(organization=organization)
    check(rows.update(role=name, level=level) == 1, "no membership to change")


def admin_elsewhere(user):
    """Make the person an admin of another organization, which their account
    also names as its organization."""
    other = Organization.objects.create(name="Another organization")
    OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=other,
        role=OrganizationRoles.ADMIN,
        level=Level.ADMIN,
        is_active=True,
    )
    user.organization = other
    return other


def send(view, method, user, workspace, data=None, record=None):
    """A request straight to the view, made in the workspace's organization."""
    organization = workspace.organization
    set_workspace_context(workspace=workspace, organization=organization, user=user)
    factory = APIRequestFactory()
    if method == "get":
        request = factory.get("/connector/")
    else:
        request = getattr(factory, method)("/connector/", data or {}, format="json")
    force_authenticate(request, user=user)
    request.organization = organization
    request.workspace = workspace
    kwargs = {"connector_id": record.pk} if record else {}
    return view.as_view()(request, **kwargs)


def saved_connector(workspace, url, **fields):
    return MCPConnector.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        name="tools-" + secrets.token_hex(6),
        server_url=url,
        **fields,
    )


def signed_in_connector(workspace):
    """A connector with its tools, a stored credential, an OAuth sign-in and
    the sign-in addresses its server advertised."""
    return saved_connector(
        workspace,
        STORED_URL,
        auth_type="oauth",
        auth_header_value=encrypt_falcon_token(KEY_VALUE),
        oauth_client_id="registered-client",
        oauth_client_secret=encrypt_falcon_token(CLIENT_VALUE),
        oauth_access_token=encrypt_falcon_token(ACCESS_VALUE),
        oauth_refresh_token=encrypt_falcon_token(REFRESH_VALUE),
        oauth_code_verifier=secrets.token_urlsafe(48),
        oauth_state=secrets.token_urlsafe(16),
        oauth_token_expires_at=timezone.now(),
        oauth_server_metadata={
            "authorization_endpoint": f"https://{calls.TOOLS_HOST}/authorize",
            "token_endpoint": f"https://{calls.TOOLS_HOST}/token",
        },
        is_verified=True,
        discovered_tools=[calls.TOOL],
        enabled_tool_names=[calls.TOOL["name"]],
    )


def row(record):
    return MCPConnector._base_manager.get(pk=record.pk)


def snapshot(record):
    """Every stored field of the connector, its credential and sign-in too."""
    stored = row(record)
    return tuple(
        getattr(stored, field.attname) for field in stored._meta.concrete_fields
    )


def added(workspace):
    rows = MCPConnector._base_manager.filter(workspace=workspace)
    return rows.filter(name=NEW_CONNECTOR["name"]).exists()


def nothing_sent(names, connects, unchecked):
    """No name was looked up, no connection opened and no request handed on."""
    return names.lookups == [] and connects == [] and unchecked == []


def add_hosts(names):
    names.add(calls.TOOLS_HOST, calls.PUBLIC_IP)
    names.add(NEW_HOST, calls.PUBLIC_IP)


# A member or a viewer is told who can, and nothing changes.


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_member_or_a_viewer_is_told_who_can_add_a_connector(
    role, self_hosted, names, connects, unchecked, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, role)

    response = send(LIST, "post", user, workspace, NEW_CONNECTOR)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == ADD_SENTENCE, "not told who can add one")
    check(not added(workspace), "a connector was added")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


@pytest.mark.parametrize("role", ["member", "viewer"])
@pytest.mark.parametrize(("view", "method", "data"), CONNECTOR_ACTIONS)
def test_a_member_or_a_viewer_is_told_who_can_change_a_connector(
    view, method, data, role, self_hosted, names, connects, unchecked, user, workspace
):
    add_hosts(names)
    record = signed_in_connector(workspace)
    before = snapshot(record)
    give_role(user, workspace.organization, role)

    response = send(view, method, user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == CHANGE_SENTENCE, "not told who can change it")
    check(snapshot(record) == before, "the connector changed")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


@pytest.mark.parametrize("data", ADDRESS_OR_CREDENTIAL)
def test_a_member_keeps_a_connectors_address_and_stored_credential(
    data, self_hosted, names, connects, unchecked, user, workspace
):
    add_hosts(names)
    record = signed_in_connector(workspace)
    give_role(user, workspace.organization, "member")

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == CHANGE_SENTENCE, "not told who can change it")
    stored = row(record)
    check(stored.server_url == STORED_URL, "the address changed")
    check(
        decrypt_falcon_token(stored.auth_header_value) == KEY_VALUE,
        "the stored credential changed",
    )
    check(
        decrypt_falcon_token(stored.oauth_access_token) == ACCESS_VALUE,
        "the stored sign-in changed",
    )
    check(stored.is_verified is True, "the connector is no longer verified")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


@pytest.mark.parametrize(("view", "method", "data"), [*ADD_ACTION, *CONNECTOR_ACTIONS])
def test_an_admin_of_another_organization_who_is_a_member_here_is_told_who_can(
    view, method, data, self_hosted, names, connects, unchecked, user, workspace
):
    add_hosts(names)
    record = signed_in_connector(workspace)
    before = snapshot(record)
    give_role(user, workspace.organization, "member")
    admin_elsewhere(user)

    target = None if view is LIST else record
    response = send(view, method, user, workspace, data, target)

    status_code = response.status_code
    assert status_code == 403
    sentence = ADD_SENTENCE if view is LIST else CHANGE_SENTENCE
    check(response.data["detail"] == sentence, "not told who can")
    check(not added(workspace), "a connector was added")
    check(snapshot(record) == before, "the connector changed")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


# A member or a viewer lists the connectors and reads one.


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_member_or_a_viewer_lists_the_connectors_and_reads_one(
    role, self_hosted, names, connects, unchecked, user, workspace
):
    record = signed_in_connector(workspace)
    stored = row(record)
    values = [KEY_VALUE, CLIENT_VALUE, ACCESS_VALUE, REFRESH_VALUE]
    values += [getattr(stored, field) for field in SIGN_IN_FIELDS]
    give_role(user, workspace.organization, role)

    listed = send(LIST, "get", user, workspace)
    read = send(DETAIL, "get", user, workspace, record=record)

    listed_status, read_status = listed.status_code, read.status_code
    assert (listed_status, read_status) == (200, 200)
    listed_ids = [item["id"] for item in listed.data["results"]]
    check(listed_ids == [str(record.pk)], "the connector is not listed")
    check(read.data["result"]["id"] == str(record.pk), "the connector is not read")
    shown = json.dumps([listed.data, read.data], default=str)
    check(not any(value in shown for value in values), "a stored value was shown")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


# An owner or an admin manages the connectors.


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_owner_or_an_admin_adds_a_connector(
    role, self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    give_role(user, workspace.organization, role)

    response = send(LIST, "post", user, workspace, NEW_CONNECTOR)

    status_code = response.status_code
    assert status_code == 201
    check(added(workspace), "the connector was not added")
    check(names.lookups == [calls.TOOLS_HOST], "the address was not checked")


@pytest.mark.parametrize("role", ["owner", "admin"])
@pytest.mark.parametrize("data", CHANGES)
def test_an_owner_or_an_admin_changes_a_connector(
    data, role, self_hosted, names, connects, user, workspace
):
    record = saved_connector(workspace, STORED_URL)
    give_role(user, workspace.organization, role)

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    for field, value in data.items():
        check(getattr(stored, field) == value, f"{field} was not changed")


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_owner_or_an_admin_moves_a_connector_and_its_sign_in_is_cleared(
    role, self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    record = signed_in_connector(workspace)
    give_role(user, workspace.organization, role)

    response = send(DETAIL, "patch", user, workspace, {"server_url": NEW_URL}, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.server_url == NEW_URL, "the address was not changed")
    for field in SIGN_IN_FIELDS:
        check(getattr(stored, field) == "", f"{field} was kept")
    check(stored.is_verified is False, "the moved connector is still verified")
    check(names.lookups == [NEW_HOST], "the new address was not checked")


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_owner_or_an_admin_replaces_a_connectors_credential(
    role, self_hosted, names, connects, user, workspace
):
    add_hosts(names)
    record = signed_in_connector(workspace)
    give_role(user, workspace.organization, role)
    data = {"auth_header_value": NEW_VALUE}

    response = send(DETAIL, "patch", user, workspace, data, record)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    check(stored.server_url == STORED_URL, "the address changed")
    check(
        decrypt_falcon_token(stored.auth_header_value) == NEW_VALUE,
        "the new credential was not kept",
    )


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_owner_or_an_admin_removes_a_connector(
    role, self_hosted, names, connects, user, workspace
):
    record = saved_connector(workspace, STORED_URL)
    give_role(user, workspace.organization, role)

    response = send(DETAIL, "delete", user, workspace, record=record)

    status_code = response.status_code
    assert status_code == 204
    check(row(record).deleted is True, "the connector was not removed")


@pytest.mark.parametrize("role", ["owner", "admin"])
@pytest.mark.parametrize("view", CHECKS)
def test_an_owner_or_an_admin_discovers_tests_and_signs_in_a_connector(
    view, role, self_hosted, names, connects, user, workspace
):
    give_role(user, workspace.organization, role)

    with calls.connector_server(names) as (server, base):
        record = saved_connector(workspace, f"{base}/mcp")
        response = send(view, "post", user, workspace, record=record)
        reached = list(server.requests)

    status_code = response.status_code
    assert status_code == 200
    check(reached != [], "the connector was not called")
    check(names.lookups != [], "the address was not checked")


# Which of a connector's discovered tools are on is a change to the connector:
# an owner or an admin turns a tool on or off, and members and viewers read them.

TOOLS = views_connectors.MCPConnectorToolsView
CONNECTOR_AS_SAVED = views_connectors.MCPConnectorDetailSerializer

SECOND_TOOL = {"name": "summarize", "description": "Sums a record up"}
DISCOVERED = [calls.TOOL, SECOND_TOOL]
ONE_ON = [calls.TOOL["name"]]
BOTH_ON = [calls.TOOL["name"], SECOND_TOOL["name"]]

# The tools on before the request, and the tools the request turns on. Neither
# list is empty, since an empty list turns every discovered tool on.
TOOL_CHOICES = [
    pytest.param(BOTH_ON, ONE_ON, id="turn-a-tool-off"),
    pytest.param(ONE_ON, BOTH_ON, id="turn-a-tool-on"),
]


def connector_with_tools(workspace, tools_on):
    """A connector with a stored credential, two discovered tools and the
    given tools on."""
    return saved_connector(
        workspace,
        STORED_URL,
        auth_type="bearer",
        auth_header_value=encrypt_falcon_token(KEY_VALUE),
        is_verified=True,
        discovered_tools=DISCOVERED,
        enabled_tool_names=tools_on,
    )


def choose_tools(user, workspace, record, tools):
    data = {"enabled_tool_names": tools}
    return send(TOOLS, "patch", user, workspace, data, record)


@pytest.mark.parametrize("role", ["member", "viewer"])
@pytest.mark.parametrize(("tools_on", "asked_for"), TOOL_CHOICES)
def test_a_member_or_a_viewer_is_told_who_can_turn_a_connectors_tools_on_or_off(
    tools_on, asked_for, role, self_hosted, names, connects, unchecked, user, workspace
):
    record = connector_with_tools(workspace, tools_on)
    before = snapshot(record)
    give_role(user, workspace.organization, role)

    response = choose_tools(user, workspace, record, asked_for)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == CHANGE_SENTENCE, "not told who can change it")
    check(row(record).enabled_tool_names == tools_on, "the tools on changed")
    check(snapshot(record) == before, "the connector changed")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


@pytest.mark.parametrize(("tools_on", "asked_for"), TOOL_CHOICES)
def test_a_member_here_who_is_an_admin_elsewhere_is_told_who_can_turn_tools_on_or_off(
    tools_on, asked_for, self_hosted, names, connects, unchecked, user, workspace
):
    record = connector_with_tools(workspace, tools_on)
    before = snapshot(record)
    give_role(user, workspace.organization, "member")
    admin_elsewhere(user)

    response = choose_tools(user, workspace, record, asked_for)

    status_code = response.status_code
    assert status_code == 403
    check(response.data["detail"] == CHANGE_SENTENCE, "not told who can change it")
    check(row(record).enabled_tool_names == tools_on, "the tools on changed")
    check(snapshot(record) == before, "the connector changed")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


@pytest.mark.parametrize("role", ["owner", "admin"])
@pytest.mark.parametrize(("tools_on", "asked_for"), TOOL_CHOICES)
def test_an_owner_or_an_admin_turns_a_connectors_tools_on_or_off(
    tools_on, asked_for, role, self_hosted, names, connects, unchecked, user, workspace
):
    record = connector_with_tools(workspace, tools_on)
    give_role(user, workspace.organization, role)

    response = choose_tools(user, workspace, record, asked_for)

    status_code = response.status_code
    assert status_code == 200
    stored = row(record)
    tools_on_now = sorted(stored.enabled_tool_names)
    check(tools_on_now == sorted(asked_for), "the tools on did not change")
    answer = {"status": True, "result": CONNECTOR_AS_SAVED(stored).data}
    check(response.data == answer, "the answer is not the connector as saved")
    check(nothing_sent(names, connects, unchecked), "a request was sent")


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_member_or_a_viewer_reads_which_of_a_connectors_tools_are_on(
    role, self_hosted, names, connects, unchecked, user, workspace
):
    record = connector_with_tools(workspace, ONE_ON)
    give_role(user, workspace.organization, role)

    listed = send(LIST, "get", user, workspace)
    read = send(DETAIL, "get", user, workspace, record=record)

    listed_status, read_status = listed.status_code, read.status_code
    assert (listed_status, read_status) == (200, 200)
    counts = [item["tool_count"] for item in listed.data["results"]]
    check(counts == [1], "the tools on are not counted")
    result = read.data["result"]
    check(result["discovered_tools"] == DISCOVERED, "the tools are not shown")
    check(result["enabled_tool_names"] == ONE_ON, "the tools on are not shown")
    check(nothing_sent(names, connects, unchecked), "a request was sent")

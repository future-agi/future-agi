"""Validating LiveKit credentials rehydrates stored secrets only from the caller's own agent.

``POST /simulate/api/livekit/validate-credentials/`` accepts masked ``api_key`` /
``api_secret`` values plus an ``agent_definition_id`` and swaps the masked values
for the agent's stored LiveKit key and secret before probing the URL in the
request. A self-hosted install still runs that probe (on Future AGI Cloud the
view answers "not verified" before any lookup, T6 #7b), so whichever agent the id
names, its secrets go to a URL the caller chose.

The agent is looked up the way every other AgentDefinition lookup in simulate
does it: ``AgentDefinition.objects`` filtered by
``organization=getattr(request, "organization", None) or request.user.organization``
and ``deleted=False`` (views/agent_definition.py AgentRunsView,
views/agent_setup.py StoredAgentView, views/agent_version.py). The ``objects``
manager (tfc/utils/base_model.py BaseModelManager) adds the workspace the request
was authenticated into (accounts/authentication.py _set_workspace_context): a
non-default workspace sees only its own agents; the default workspace also sees
the organization's default-workspace and workspace-less agents. Another
organization's agent, an agent in a workspace the caller is not in, a deleted
agent and an id that names nothing all get the same 404, before any credential
is read or any request is sent.

DB-free: AgentDefinition rows live in memory and every query the view builds is
answered by evaluating its real WHERE tree against them (any other model, an
unsupported lookup or a real database access fails the test). The LiveKit client
is a recording fake and the voice-simulation plan gate is stubbed open.
"""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.db.models.lookups import Exact, IsNull
from django.db.models.query import QuerySet
from django.db.models.sql.where import NothingNode, WhereNode
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Organization, Workspace
from simulate.models import AgentDefinition
from simulate.views.livekit_api import ValidateLiveKitCredentialsView
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

PATH = "/simulate/api/livekit/validate-credentials/"
CALLER_URL = "wss://livekit.attacker.example"
MASKED_KEY = "abcd...wxyz"
MASKED_SECRET = "********"
NOT_VERIFIED = (
    "LiveKit credentials not verified on Future AGI Cloud: Future AGI does not "
    "contact customer LiveKit URLs from its servers; connection problems will "
    "show when a run connects."
)

AGENT_TABLE = AgentDefinition._meta.db_table
WORKSPACE_TABLE = Workspace._meta.db_table


# --------------------------------------------------------------------------
# In-memory simulate_agent_definition
# --------------------------------------------------------------------------


def _matches(node, columns, query):
    """Evaluate a WHERE tree against one row; refuse anything not understood."""
    if isinstance(node, NothingNode):
        return False
    if isinstance(node, WhereNode):
        results = [_matches(child, columns, query) for child in node.children]
        value = all(results) if node.connector == "AND" else any(results)
        return value != node.negated
    table = query.alias_map[node.lhs.alias].table_name
    key = (table, node.lhs.target.column)
    assert key in columns, f"query filters on an unmodelled column {key}"
    actual = columns[key]
    if isinstance(node, IsNull):  # includes RelatedIsNull
        return (actual is None) == bool(node.rhs)
    if isinstance(node, Exact):  # includes RelatedExact
        return actual is not None and actual == node.rhs
    raise AssertionError(f"unsupported lookup {type(node).__name__} on {key}")


def _constraints(where):
    """The column = value constraints every matching row must meet (top-level AND)."""
    assert where.connector == "AND" and not where.negated
    found = set()
    for child in where.children:
        if isinstance(child, Exact):
            found.add((child.lhs.target.column, child.rhs))
    return found


class AgentTable:
    def __init__(self):
        self.rows = []
        self.queries = []

    def add(self, *, organization, workspace, key, secret, deleted=False):
        credentials = MagicMock(name=f"credentials<{key}>")
        credentials.get_api_key.return_value = key
        credentials.get_api_secret.return_value = secret
        agent = SimpleNamespace(
            id=uuid.uuid4(),
            active_version=SimpleNamespace(credentials=credentials),
            latest_version=None,
            credentials=credentials,
        )
        self.rows.append(
            (
                {
                    (AGENT_TABLE, "id"): agent.id,
                    (AGENT_TABLE, "organization_id"): organization.id,
                    (AGENT_TABLE, "workspace_id"): workspace.id if workspace else None,
                    (AGENT_TABLE, "deleted"): deleted,
                    # LEFT JOIN accounts_workspace: NULLs for a workspace-less agent.
                    (WORKSPACE_TABLE, "is_default"): (
                        workspace.is_default if workspace else None
                    ),
                    (WORKSPACE_TABLE, "organization_id"): (
                        workspace.organization_id if workspace else None
                    ),
                },
                agent,
            )
        )
        return agent


@pytest.fixture
def agents():
    table = AgentTable()

    def fetch_all(queryset):
        if queryset._result_cache is not None:
            return
        assert queryset.model is AgentDefinition, (
            f"unexpected query on {queryset.model.__name__}"
        )
        query = queryset.query
        table.queries.append(query)
        found = [
            agent
            for columns, agent in table.rows
            if _matches(query.where, columns, query)
        ]
        queryset._result_cache = found[query.low_mark : query.high_mark]
        queryset._prefetch_done = True

    with patch.object(QuerySet, "_fetch_all", fetch_all):
        yield table


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


@pytest.fixture
def tenancy():
    org_a = Organization(id=uuid.uuid4(), name="Org A")
    org_b = Organization(id=uuid.uuid4(), name="Org B")
    return SimpleNamespace(
        org_a=org_a,
        org_b=org_b,
        a_default=Workspace(
            id=uuid.uuid4(), name="A default", organization=org_a, is_default=True
        ),
        a_one=Workspace(
            id=uuid.uuid4(), name="A one", organization=org_a, is_default=False
        ),
        a_two=Workspace(
            id=uuid.uuid4(), name="A two", organization=org_a, is_default=False
        ),
        b_default=Workspace(
            id=uuid.uuid4(), name="B default", organization=org_b, is_default=True
        ),
    )


@pytest.fixture(autouse=True)
def no_ambient_workspace():
    clear_workspace_context()
    yield
    clear_workspace_context()


@pytest.fixture(autouse=True)
def voice_plan_open():
    with patch("tfc.ee_gates.voice_sim_gate_response", return_value=None):
        yield


@pytest.fixture(autouse=True)
def self_hosted(settings):
    settings.CLOUD_DEPLOYMENT = ""


@pytest.fixture
def livekit():
    client = MagicMock()
    client.room.create_room = AsyncMock()
    client.room.delete_room = AsyncMock()
    client.agent_dispatch.create_dispatch = AsyncMock()
    client.aclose = AsyncMock()
    fake = SimpleNamespace(
        LiveKitAPI=MagicMock(return_value=client),
        CreateRoomRequest=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
        DeleteRoomRequest=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
        CreateAgentDispatchRequest=MagicMock(
            side_effect=lambda **kw: SimpleNamespace(**kw)
        ),
        client=client,
    )
    with patch("tfc.utils.lazy_extras.load_extra", return_value=fake) as load:
        fake.load = load
        yield fake


def _post(body, *, organization, workspace=None):
    """POST as a member of ``organization``.

    With a ``workspace`` the request is bound the way APIKeyAuthentication binds
    it (request.organization, request.workspace and the model-manager context).
    Without one nothing is bound and ``request.user.organization`` is all the
    view has, as for any caller that reaches it with no workspace context.
    """
    request = APIRequestFactory().post(PATH, body, format="json")
    user = SimpleNamespace(
        id=uuid.uuid4(),
        is_authenticated=True,
        organization=organization,
        email="member@example.com",
    )
    if workspace is not None:
        request.organization = organization
        request.workspace = workspace
        set_workspace_context(
            workspace=workspace,
            organization=organization,
            user=user,
            require_workspace=True,
        )
    force_authenticate(request, user=user)
    try:
        return ValidateLiveKitCredentialsView.as_view()(request)
    finally:
        clear_workspace_context()


def _masked(agent_definition_id, **overrides):
    body = {
        "livekit_url": CALLER_URL,
        "api_key": MASKED_KEY,
        "api_secret": MASKED_SECRET,
        "agent_name": "returns-agent",
        "agent_definition_id": str(agent_definition_id),
    }
    body.update(overrides)
    return body


def _assert_not_found_and_nothing_sent(response, made_up, livekit, *stored):
    for agent in stored:
        assert agent.credentials.mock_calls == []
    livekit.LiveKitAPI.assert_not_called()
    livekit.client.room.create_room.assert_not_awaited()
    livekit.client.agent_dispatch.create_dispatch.assert_not_awaited()
    assert response.status_code == 404
    assert made_up.status_code == 404
    assert response.data == made_up.data
    assert "Agent definition not found" in str(response.data)


# --------------------------------------------------------------------------
# (1) Another organization's agent
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_another_orgs_agent_id_gets_the_same_404_as_a_made_up_id(
    agents, tenancy, livekit, bound
):
    agents.add(
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
        key="APIorg-a-key",
        secret="org-a-secret",
    )
    foreign = agents.add(
        organization=tenancy.org_b,
        workspace=tenancy.b_default,
        key="APIorg-b-key",
        secret="org-b-secret-must-not-leave",
    )
    caller = {
        "organization": tenancy.org_a,
        "workspace": tenancy.a_one if bound else None,
    }

    response = _post(_masked(foreign.id), **caller)
    made_up = _post(_masked(uuid.uuid4()), **caller)

    _assert_not_found_and_nothing_sent(response, made_up, livekit, foreign)


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_another_orgs_workspace_less_agent_gets_the_same_404(
    agents, tenancy, livekit, bound
):
    """A workspace-less agent of org B: the default-workspace branch of the
    manager admits workspace-less rows only of the caller's organization."""
    foreign = agents.add(
        organization=tenancy.org_b,
        workspace=None,
        key="APIorg-b-key",
        secret="org-b-secret-must-not-leave",
    )
    caller = {
        "organization": tenancy.org_a,
        "workspace": tenancy.a_default if bound else None,
    }

    response = _post(_masked(foreign.id), **caller)
    made_up = _post(_masked(uuid.uuid4()), **caller)

    _assert_not_found_and_nothing_sent(response, made_up, livekit, foreign)


def test_the_lookup_is_filtered_by_the_callers_organization(agents, tenancy, livekit):
    """Without a bound workspace the manager adds no tenant filter at all, so the
    view's own filter is the boundary: id, the caller's organization, not deleted."""
    requested = uuid.uuid4()

    _post(_masked(requested), organization=tenancy.org_a)

    (query,) = agents.queries
    assert {
        ("id", requested),
        ("organization_id", tenancy.org_a.id),
        ("deleted", False),
    } <= _constraints(query.where)


def test_the_lookup_is_filtered_by_the_callers_workspace(agents, tenancy, livekit):
    requested = uuid.uuid4()

    _post(_masked(requested), organization=tenancy.org_a, workspace=tenancy.a_one)

    (query,) = agents.queries
    assert {
        ("id", requested),
        ("organization_id", tenancy.org_a.id),
        ("workspace_id", tenancy.a_one.id),
        ("deleted", False),
    } <= _constraints(query.where)


# --------------------------------------------------------------------------
# (2) Same organization, another workspace; a deleted agent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caller_workspace", ["a_one", "a_default"], ids=["non-default", "default"]
)
def test_an_agent_in_another_workspace_of_the_org_gets_the_same_404(
    agents, tenancy, livekit, caller_workspace
):
    sibling = agents.add(
        organization=tenancy.org_a,
        workspace=tenancy.a_two,
        key="APIworkspace-two-key",
        secret="workspace-two-secret",
    )
    caller = {
        "organization": tenancy.org_a,
        "workspace": getattr(tenancy, caller_workspace),
    }

    response = _post(_masked(sibling.id), **caller)
    made_up = _post(_masked(uuid.uuid4()), **caller)

    _assert_not_found_and_nothing_sent(response, made_up, livekit, sibling)


@pytest.mark.parametrize("bound", [True, False], ids=["bound-workspace", "unbound"])
def test_a_deleted_agent_gets_the_same_404(agents, tenancy, livekit, bound):
    deleted = agents.add(
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
        key="APIdeleted-key",
        secret="deleted-secret",
        deleted=True,
    )
    caller = {
        "organization": tenancy.org_a,
        "workspace": tenancy.a_one if bound else None,
    }

    response = _post(_masked(deleted.id), **caller)
    made_up = _post(_masked(uuid.uuid4()), **caller)

    _assert_not_found_and_nothing_sent(response, made_up, livekit, deleted)


# --------------------------------------------------------------------------
# (3) The caller's own agent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent_workspace", "caller_workspace"),
    [
        ("a_one", "a_one"),
        ("a_default", "a_default"),
        (None, "a_default"),
        ("a_one", None),
    ],
    ids=["same-workspace", "default-workspace", "workspace-less", "unbound"],
)
def test_own_agent_stored_credentials_are_rehydrated_and_probed(
    agents, tenancy, livekit, agent_workspace, caller_workspace
):
    own = agents.add(
        organization=tenancy.org_a,
        workspace=getattr(tenancy, agent_workspace) if agent_workspace else None,
        key="APIstored-key",
        secret="stored-secret",
    )

    response = _post(
        _masked(own.id),
        organization=tenancy.org_a,
        workspace=getattr(tenancy, caller_workspace) if caller_workspace else None,
    )

    own.credentials.get_api_key.assert_called_once_with()
    own.credentials.get_api_secret.assert_called_once_with()
    livekit.load.assert_called_once_with("livekit.api", "voice")
    livekit.LiveKitAPI.assert_called_once_with(
        url="https://livekit.attacker.example",
        api_key="APIstored-key",
        api_secret="stored-secret",
    )
    livekit.client.room.create_room.assert_awaited_once()
    livekit.client.room.delete_room.assert_awaited_once()
    assert response.status_code == 200
    assert response.data["result"] == {"valid": True}


def test_own_agent_rehydrates_only_the_masked_field(agents, tenancy, livekit):
    own = agents.add(
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
        key="APIstored-key",
        secret="stored-secret",
    )

    response = _post(
        _masked(own.id, api_secret="typed-new-secret"),
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
    )

    own.credentials.get_api_key.assert_called_once_with()
    own.credentials.get_api_secret.assert_not_called()
    livekit.LiveKitAPI.assert_called_once_with(
        url="https://livekit.attacker.example",
        api_key="APIstored-key",
        api_secret="typed-new-secret",
    )
    assert response.status_code == 200


def test_plaintext_credentials_never_look_up_the_agent(agents, tenancy, livekit):
    """Nothing masked, nothing to rehydrate: the id is not looked up, so it
    answers nothing about which agents exist."""
    foreign = agents.add(
        organization=tenancy.org_b,
        workspace=tenancy.b_default,
        key="APIorg-b-key",
        secret="org-b-secret",
    )

    response = _post(
        _masked(foreign.id, api_key="APItyped-key", api_secret="typed-secret"),
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
    )

    assert agents.queries == []
    assert foreign.credentials.mock_calls == []
    livekit.LiveKitAPI.assert_called_once_with(
        url="https://livekit.attacker.example",
        api_key="APItyped-key",
        api_secret="typed-secret",
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------
# (4) Future AGI Cloud (T6 #7b) is unchanged
# --------------------------------------------------------------------------


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV"])
@pytest.mark.parametrize("which", ["own", "foreign", "made-up"])
def test_cloud_answers_not_verified_without_any_lookup(
    settings, agents, tenancy, livekit, deployment, which
):
    settings.CLOUD_DEPLOYMENT = deployment
    own = agents.add(
        organization=tenancy.org_a,
        workspace=tenancy.a_one,
        key="APIorg-a-key",
        secret="org-a-secret",
    )
    foreign = agents.add(
        organization=tenancy.org_b,
        workspace=tenancy.b_default,
        key="APIorg-b-key",
        secret="org-b-secret",
    )
    agent_id = {"own": own.id, "foreign": foreign.id, "made-up": uuid.uuid4()}[which]

    response = _post(
        _masked(agent_id), organization=tenancy.org_a, workspace=tenancy.a_one
    )

    assert agents.queries == []
    assert own.credentials.mock_calls == []
    assert foreign.credentials.mock_calls == []
    livekit.load.assert_not_called()
    livekit.LiveKitAPI.assert_not_called()
    assert response.status_code == 200
    assert response.data["result"] == {
        "valid": False,
        "verified": False,
        "skipped": True,
        "message": NOT_VERIFIED,
    }


def test_validate_contract_documents_the_not_found_answer():
    swagger = Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    responses = json.loads(swagger.read_text())["paths"][PATH]["post"]["responses"]

    assert responses["404"]["schema"] == {"$ref": "#/definitions/ApiTextErrorResponse"}

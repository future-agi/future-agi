"""TH-8216 B1: Workbench graph server invariants and tenant denial.

R-S03 (TH-8413): template graphs are read-only for tenants, a foreign
graph/version/node/port/connection answers exactly like a missing one, and
graph metadata writes are atomic.

Requests go through the same path as the other view tests in this app
(``authenticated_client``: ``force_authenticate`` + workspace injection).
"""

import copy
import uuid

import pytest
from django.db.models.signals import post_save
from django.urls import reverse
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from agent_playground.models.choices import GraphVersionStatus, NodeType, PortDirection
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.port import Port

TEMPLATE_QUERY = "?is_template=true"


def _replace(value, old, new):
    if isinstance(value, dict):
        return {k: _replace(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace(v, old, new) for v in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def _assert_same_answer(response, missing_response, *, swap=None):
    """Same status and byte-for-byte body, modulo the echoed id in ``swap``."""
    body = response.json()
    if swap:
        body = _replace(body, *swap)
    assert (response.status_code, body) == (
        missing_response.status_code,
        missing_response.json(),
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def foreign(db, node_template):
    """Another tenant's graph with a draft version, two nodes, a port and a connection."""
    org = Organization.objects.create(name="Foreign Organization")
    user = User.objects.create_user(
        email="foreign@futureagi.com",
        password="testpassword123",
        name="Foreign User",
        organization=org,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user, organization=org, defaults={"is_active": True}
    )
    workspace = Workspace.objects.create(
        name="Foreign Workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    graph = Graph.no_workspace_objects.create(
        organization=org,
        workspace=workspace,
        name="Foreign Graph",
        created_by=user,
    )
    draft = GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status=GraphVersionStatus.DRAFT
    )
    GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=2, status=GraphVersionStatus.INACTIVE
    )
    first = Node.no_workspace_objects.create(
        graph_version=draft,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Foreign Node A",
        config={},
    )
    second = Node.no_workspace_objects.create(
        graph_version=draft,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Foreign Node B",
        config={},
    )
    port = Port.no_workspace_objects.create(
        node=first,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
    )
    connection = NodeConnection.no_workspace_objects.create(
        graph_version=draft, source_node=first, target_node=second
    )
    return {
        "graph": graph,
        "version": draft,
        "node": first,
        "node_b": second,
        "port": port,
        "nc": connection,
    }


def _foreign_state(foreign):
    graph = Graph.all_objects.get(pk=foreign["graph"].pk)
    return {
        "graph": (graph.name, graph.description, graph.deleted),
        "versions": sorted(
            GraphVersion.all_objects.filter(graph=graph).values_list(
                "id", "status", "commit_message", "deleted"
            )
        ),
        "nodes": sorted(
            Node.all_objects.filter(graph_version__graph=graph).values_list(
                "id", "name", "deleted"
            )
        ),
        "ports": sorted(
            Port.all_objects.filter(node__graph_version__graph=graph).values_list(
                "id", "display_name", "deleted"
            )
        ),
        "ncs": sorted(
            NodeConnection.all_objects.filter(graph_version__graph=graph).values_list(
                "id", "deleted"
            )
        ),
    }


@pytest.fixture
def template_versions(template_graph):
    """A system template with an active, an inactive and a draft version."""
    return {
        "active": GraphVersion.no_workspace_objects.create(
            graph=template_graph, version_number=1, status=GraphVersionStatus.ACTIVE
        ),
        "inactive": GraphVersion.no_workspace_objects.create(
            graph=template_graph, version_number=2, status=GraphVersionStatus.INACTIVE
        ),
        "draft": GraphVersion.no_workspace_objects.create(
            graph=template_graph, version_number=3, status=GraphVersionStatus.DRAFT
        ),
    }


def _template_state(template_graph):
    graph = Graph.all_objects.get(pk=template_graph.pk)
    return {
        "graph": (
            graph.name,
            graph.description,
            graph.deleted,
            graph.organization_id,
            graph.workspace_id,
        ),
        "versions": sorted(
            GraphVersion.all_objects.filter(graph=graph).values_list(
                "id", "status", "commit_message", "deleted"
            )
        ),
    }


# =============================================================================
# R-S03: foreign IDs answer exactly like missing IDs
# =============================================================================

GRAPH = "/agent-playground/graphs/{g}/"
VERSIONS = "/agent-playground/graphs/{g}/versions/"
VERSION = "/agent-playground/graphs/{g}/versions/{v}/"
ACTIVATE = "/agent-playground/graphs/{g}/versions/{v}/activate/"
NODES = "/agent-playground/graphs/{g}/versions/{v}/nodes/"
NODE = "/agent-playground/graphs/{g}/versions/{v}/nodes/{n}/"
MAPPINGS = "/agent-playground/graphs/{g}/versions/{v}/nodes/{n}/possible-edge-mappings/"
PORT = "/agent-playground/graphs/{g}/versions/{v}/ports/{p}/"
NCS = "/agent-playground/graphs/{g}/versions/{v}/node-connections/"
NC = "/agent-playground/graphs/{g}/versions/{v}/node-connections/{c}/"
REFERENCEABLE = "/agent-playground/graphs/{g}/referenceable-graphs/"


def _node_payload(ids):
    return {
        "id": str(uuid.uuid4()),
        "type": "atomic",
        "name": "Intruder",
        "node_template_id": ids["template"],
    }


def _nc_payload(ids):
    return {
        "id": str(uuid.uuid4()),
        "source_node_id": ids["n"],
        "target_node_id": ids["n2"],
    }


# (name, method, path, payload builder)
ENDPOINTS = [
    ("graph-retrieve", "get", GRAPH, None),
    ("graph-put", "put", GRAPH, lambda ids: {"name": "hijacked"}),
    ("graph-patch", "patch", GRAPH, lambda ids: {"name": "hijacked"}),
    ("graph-delete", "delete", GRAPH, None),
    ("graph-referenceable", "get", REFERENCEABLE, None),
    ("versions-list", "get", VERSIONS, None),
    ("versions-create", "post", VERSIONS, lambda ids: {}),
    ("version-retrieve", "get", VERSION, None),
    ("version-put", "put", VERSION, lambda ids: {"commit_message": "hijacked"}),
    ("version-patch", "patch", VERSION, lambda ids: {"status": "active"}),
    ("version-delete", "delete", VERSION, None),
    ("version-activate", "post", ACTIVATE, None),
    ("node-create", "post", NODES, _node_payload),
    ("node-retrieve", "get", NODE, None),
    ("node-patch", "patch", NODE, lambda ids: {"name": "hijacked"}),
    ("node-delete", "delete", NODE, None),
    ("node-mappings", "get", MAPPINGS, None),
    ("port-patch", "patch", PORT, lambda ids: {"display_name": "hijacked"}),
    ("nc-create", "post", NCS, _nc_payload),
    ("nc-delete", "delete", NC, None),
]


def _call(client, method, path, ids, payload_builder, query=""):
    url = path.format(**ids) + query
    kwargs = {"format": "json"}
    if payload_builder is not None:
        kwargs["data"] = payload_builder(ids)
    return getattr(client, method)(url, **kwargs)


def _missing_ids(template_id):
    return {
        "g": str(uuid.uuid4()),
        "v": str(uuid.uuid4()),
        "n": str(uuid.uuid4()),
        "n2": str(uuid.uuid4()),
        "p": str(uuid.uuid4()),
        "c": str(uuid.uuid4()),
        "template": template_id,
    }


def _foreign_ids(foreign, template_id):
    return {
        "g": str(foreign["graph"].id),
        "v": str(foreign["version"].id),
        "n": str(foreign["node"].id),
        "n2": str(foreign["node_b"].id),
        "p": str(foreign["port"].id),
        "c": str(foreign["nc"].id),
        "template": template_id,
    }


@pytest.mark.parametrize(
    "name,method,path,payload", ENDPOINTS, ids=[e[0] for e in ENDPOINTS]
)
def test_rs03_foreign_graph_reads_like_missing(
    authenticated_client, graph, foreign, node_template, name, method, path, payload
):
    """Every graph-scoped endpoint: another tenant's graph id == a random id."""
    before = _foreign_state(foreign)
    template_id = str(node_template.id)

    foreign_response = _call(
        authenticated_client, method, path, _foreign_ids(foreign, template_id), payload
    )
    missing_response = _call(
        authenticated_client, method, path, _missing_ids(template_id), payload
    )

    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    _assert_same_answer(foreign_response, missing_response)
    assert _foreign_state(foreign) == before


VERSION_SCOPED = [e for e in ENDPOINTS if "{v}" in e[2]]


@pytest.mark.parametrize(
    "name,method,path,payload", VERSION_SCOPED, ids=[e[0] for e in VERSION_SCOPED]
)
def test_rs03_foreign_version_under_own_graph_reads_like_missing(
    authenticated_client,
    graph,
    graph_version,
    foreign,
    node_template,
    name,
    method,
    path,
    payload,
):
    """Own graph id + another tenant's version id == own graph id + a random id."""
    before = _foreign_state(foreign)
    template_id = str(node_template.id)
    foreign_ids = _foreign_ids(foreign, template_id) | {"g": str(graph.id)}
    missing_ids = _missing_ids(template_id) | {"g": str(graph.id)}

    foreign_response = _call(authenticated_client, method, path, foreign_ids, payload)
    missing_response = _call(authenticated_client, method, path, missing_ids, payload)

    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    _assert_same_answer(foreign_response, missing_response)
    assert _foreign_state(foreign) == before


CHILD_SCOPED = [
    e
    for e in ENDPOINTS
    if any(key in e[2] for key in ("{n}", "{p}", "{c}")) or e[0] == "nc-create"
]


@pytest.mark.parametrize(
    "name,method,path,payload", CHILD_SCOPED, ids=[e[0] for e in CHILD_SCOPED]
)
def test_rs03_foreign_node_port_connection_reads_like_missing(
    authenticated_client,
    graph,
    graph_version,
    foreign,
    node_template,
    name,
    method,
    path,
    payload,
):
    """Own draft version + another tenant's node/port/connection == a random id."""
    before = _foreign_state(foreign)
    template_id = str(node_template.id)
    own = {"g": str(graph.id), "v": str(graph_version.id)}
    foreign_ids = _foreign_ids(foreign, template_id) | own
    missing_ids = _missing_ids(template_id) | own

    foreign_response = _call(authenticated_client, method, path, foreign_ids, payload)
    missing_response = _call(authenticated_client, method, path, missing_ids, payload)

    _assert_same_answer(foreign_response, missing_response)
    assert foreign_response.status_code in (
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_404_NOT_FOUND,
    )
    assert _foreign_state(foreign) == before


def test_rs03_bulk_delete_foreign_id_reads_like_missing(
    authenticated_client, graph, foreign
):
    url = reverse("graph-bulk-delete")
    before = _foreign_state(foreign)
    foreign_id = str(foreign["graph"].id)
    missing_id = str(uuid.uuid4())

    foreign_response = authenticated_client.post(
        url, {"ids": [foreign_id]}, format="json"
    )
    missing_response = authenticated_client.post(
        url, {"ids": [missing_id]}, format="json"
    )

    assert foreign_response.status_code == status.HTTP_404_NOT_FOUND
    _assert_same_answer(
        foreign_response, missing_response, swap=(foreign_id, missing_id)
    )
    assert _foreign_state(foreign) == before


def test_rs03_list_never_includes_foreign_graphs(authenticated_client, graph, foreign):
    response = authenticated_client.get(reverse("graph-list"))
    assert response.status_code == status.HTTP_200_OK
    ids = {row["id"] for row in response.json()["result"]["graphs"]}
    assert ids == {str(graph.id)}


# =============================================================================
# R-S03 / TH-8413: ?is_template=true is honoured for safe reads only
# =============================================================================

TEMPLATE_WRITES = [
    ("graph-put", "put", GRAPH, None, {"name": "hijacked"}),
    ("graph-patch", "patch", GRAPH, None, {"name": "hijacked"}),
    ("graph-delete", "delete", GRAPH, None, None),
    ("versions-create", "post", VERSIONS, None, {}),
    ("version-put", "put", VERSION, "draft", {"commit_message": "hijacked"}),
    ("version-patch-commit", "patch", VERSION, "draft", {"status": "active"}),
    ("version-delete", "delete", VERSION, "inactive", None),
    ("version-activate", "post", ACTIVATE, "inactive", None),
]


@pytest.mark.parametrize(
    "name,method,path,version_key,payload",
    TEMPLATE_WRITES,
    ids=[w[0] for w in TEMPLATE_WRITES],
)
def test_th8413_template_write_reads_like_missing_graph(
    authenticated_client,
    graph,
    template_graph,
    template_versions,
    name,
    method,
    path,
    version_key,
    payload,
):
    """A tenant write with ?is_template=true on a system template is a 404 miss."""
    before = _template_state(template_graph)
    version_id = (
        str(template_versions[version_key].id) if version_key else str(uuid.uuid4())
    )
    target = path.format(g=template_graph.id, v=version_id) + TEMPLATE_QUERY
    missing = path.format(g=uuid.uuid4(), v=uuid.uuid4()) + TEMPLATE_QUERY

    response = getattr(authenticated_client, method)(
        target, data=copy.deepcopy(payload), format="json"
    )
    missing_response = getattr(authenticated_client, method)(
        missing, data=copy.deepcopy(payload), format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    _assert_same_answer(response, missing_response)
    assert _template_state(template_graph) == before


@pytest.mark.parametrize(
    "name,method,path,version_key,payload",
    TEMPLATE_WRITES,
    ids=[w[0] for w in TEMPLATE_WRITES],
)
def test_th8413_template_write_on_foreign_graph_reads_like_missing(
    authenticated_client,
    graph,
    foreign,
    name,
    method,
    path,
    version_key,
    payload,
):
    """?is_template=true never widens the scope to another tenant's graph."""
    before = _foreign_state(foreign)
    version = foreign["version"]
    if version_key == "inactive":
        version = GraphVersion.no_workspace_objects.get(
            graph=foreign["graph"], status=GraphVersionStatus.INACTIVE
        )
    target = path.format(g=foreign["graph"].id, v=version.id) + TEMPLATE_QUERY
    missing = path.format(g=uuid.uuid4(), v=uuid.uuid4()) + TEMPLATE_QUERY

    response = getattr(authenticated_client, method)(
        target, data=copy.deepcopy(payload), format="json"
    )
    missing_response = getattr(authenticated_client, method)(
        missing, data=copy.deepcopy(payload), format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    _assert_same_answer(response, missing_response)
    assert _foreign_state(foreign) == before


def test_th8413_template_write_on_own_graph_reads_like_missing(
    authenticated_client, graph, graph_version
):
    """?is_template=true on a write selects templates only, and none is writable."""
    url = GRAPH.format(g=graph.id) + TEMPLATE_QUERY
    missing = GRAPH.format(g=uuid.uuid4()) + TEMPLATE_QUERY

    response = authenticated_client.patch(url, {"name": "renamed"}, format="json")
    missing_response = authenticated_client.patch(
        missing, {"name": "renamed"}, format="json"
    )

    _assert_same_answer(response, missing_response)
    graph.refresh_from_db()
    assert graph.name == "Test Graph"


def test_th8413_bulk_delete_template_id_reads_like_missing(
    authenticated_client, graph, template_graph, template_versions
):
    url = reverse("graph-bulk-delete") + TEMPLATE_QUERY
    before = _template_state(template_graph)
    template_id = str(template_graph.id)
    missing_id = str(uuid.uuid4())

    response = authenticated_client.post(url, {"ids": [template_id]}, format="json")
    missing_response = authenticated_client.post(
        url, {"ids": [missing_id]}, format="json"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    _assert_same_answer(response, missing_response, swap=(template_id, missing_id))
    assert _template_state(template_graph) == before


def test_th8413_bulk_delete_select_all_never_reaches_templates(
    authenticated_client, graph, template_graph, template_versions
):
    url = reverse("graph-bulk-delete") + TEMPLATE_QUERY
    before = _template_state(template_graph)

    response = authenticated_client.post(url, {"select_all": True}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert _template_state(template_graph) == before
    graph.refresh_from_db()
    assert graph.deleted is False


def test_th8413_template_reads_stay_allowed(
    authenticated_client, graph, template_graph, template_versions
):
    """List, retrieve and version list/detail keep serving system templates."""
    listed = authenticated_client.get(reverse("graph-list") + TEMPLATE_QUERY)
    assert listed.status_code == status.HTTP_200_OK
    assert [row["id"] for row in listed.json()["result"]["graphs"]] == [
        str(template_graph.id)
    ]

    detail = authenticated_client.get(
        GRAPH.format(g=template_graph.id) + TEMPLATE_QUERY
    )
    assert detail.status_code == status.HTTP_200_OK
    assert detail.json()["result"]["is_template"] is True

    versions = authenticated_client.get(
        VERSIONS.format(g=template_graph.id) + TEMPLATE_QUERY
    )
    assert versions.status_code == status.HTTP_200_OK
    assert len(versions.json()["result"]["versions"]) == 3

    version = authenticated_client.get(
        VERSION.format(g=template_graph.id, v=template_versions["active"].id)
        + TEMPLATE_QUERY
    )
    assert version.status_code == status.HTTP_200_OK
    assert version.json()["result"]["status"] == GraphVersionStatus.ACTIVE


def test_th8413_template_needs_the_flag_even_for_reads(
    authenticated_client, graph, template_graph, template_versions
):
    """Without ?is_template=true a template id is a miss (unchanged)."""
    response = authenticated_client.get(GRAPH.format(g=template_graph.id))
    missing_response = authenticated_client.get(GRAPH.format(g=uuid.uuid4()))
    _assert_same_answer(response, missing_response)


# =============================================================================
# Atomic graph metadata writes
# =============================================================================


@pytest.fixture
def failing_graph_post_save():
    def _boom(sender, instance, **kwargs):
        raise RuntimeError("th8216b post_save failure")

    post_save.connect(_boom, sender=Graph, dispatch_uid="th8216b-boom")
    yield
    post_save.disconnect(sender=Graph, dispatch_uid="th8216b-boom")


@pytest.mark.parametrize("method", ["put", "patch"])
def test_graph_metadata_update_is_atomic(
    authenticated_client, graph, graph_version, failing_graph_post_save, method
):
    """A failing post-save signal leaves no partial metadata write."""
    response = getattr(authenticated_client, method)(
        GRAPH.format(g=graph.id),
        {"name": "partial", "description": "partial"},
        format="json",
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    stored = Graph.no_workspace_objects.get(pk=graph.pk)
    assert (stored.name, stored.description) == ("Test Graph", "A test graph")


def test_graph_destroy_is_atomic(
    authenticated_client, graph, graph_version, failing_graph_post_save
):
    response = authenticated_client.delete(GRAPH.format(g=graph.id))

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert Graph.no_workspace_objects.filter(pk=graph.pk).exists()
    assert GraphVersion.no_workspace_objects.filter(pk=graph_version.pk).exists()

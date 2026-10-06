"""TH-8216 B1: captured Workbench graph/version write responses vs swagger.json.

Same pattern as ``test_th8216_graph_contract_parity.py``: real rows, real
endpoints, sanitised captures under ``fixtures/contracts/th8216b/captured/``
and parity against the checked-in swagger.json. Covers every status/body that
TH-8216 B1 changed (TH-8413 template writes, foreign connection nodes) and the
R-A01 transition/deletion bodies it declares.
Regenerate captures with ``TH8216_REGENERATE_CAPTURES=1``.
"""

import uuid
from pathlib import Path

import pytest

from agent_playground.models.choices import GraphVersionStatus, NodeType
from agent_playground.models.graph_version import GraphVersion
from tfc.tests.openapi_parity import (
    assert_capture,
    assert_response_matches_contract,
    capture_record,
    load_swagger,
)

CAPTURED = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "contracts"
    / "th8216b"
    / "captured"
)

GRAPH = "/agent-playground/graphs/{id}/"
BULK_DELETE = "/agent-playground/graphs/delete/"
VERSIONS = "/agent-playground/graphs/{id}/versions/"
VERSION = "/agent-playground/graphs/{id}/versions/{version_id}/"
ACTIVATE = "/agent-playground/graphs/{id}/versions/{version_id}/activate/"
NCS = "/agent-playground/graphs/{id}/versions/{version_id}/node-connections/"


@pytest.fixture(scope="module")
def swagger():
    return load_swagger()


def _sorted_nodes(body):
    """Version reads do not order nodes or ports; sort for captures."""
    result = body.get("result")
    if isinstance(result, dict) and isinstance(result.get("nodes"), list):
        for node in result["nodes"]:
            node["ports"] = sorted(
                node["ports"], key=lambda port: (port["direction"], port["key"])
            )
        result["nodes"] = sorted(result["nodes"], key=lambda node: node["name"])
    return body


def _check(swagger, name, operation_id, method, path, response, **capture):
    capture.setdefault("normalize", _sorted_nodes)
    assert_capture(
        CAPTURED / f"{name}.json",
        capture_record(operation_id, method, path, response, **capture),
    )
    assert_response_matches_contract(swagger, path, method, response)


def _url(path, **ids):
    return path.format(**{k: str(v) for k, v in ids.items()})


@pytest.fixture
def inactive_graph_version(db, graph):
    return GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=3, status=GraphVersionStatus.INACTIVE, tags=[]
    )


# ── TH-8413 / R-S03: changed statuses ────────────────────────────────────────


def test_th8413_template_graph_patch_is_a_declared_404(
    swagger, authenticated_client, graph, template_graph
):
    response = authenticated_client.patch(
        _url(GRAPH, id=template_graph.id) + "?is_template=true",
        {"name": "hijacked"},
        format="json",
    )

    assert response.status_code == 404
    _check(
        swagger,
        "th8413_template_graph_patch_404",
        "agent-playground_graphs_partial_update",
        "patch",
        GRAPH,
        response,
        request={"query": {"is_template": "true"}, "body": {"name": "hijacked"}},
        note="Template writes answer like a missing graph (was 500 after a partial write).",
    )


def test_th8413_template_version_activate_is_a_declared_404(
    swagger, authenticated_client, graph, template_graph
):
    inactive = GraphVersion.no_workspace_objects.create(
        graph=template_graph, version_number=2, status=GraphVersionStatus.INACTIVE
    )

    response = authenticated_client.post(
        _url(ACTIVATE, id=template_graph.id, version_id=inactive.id)
        + "?is_template=true"
    )

    assert response.status_code == 404
    _check(
        swagger,
        "th8413_template_version_activate_404",
        "agent-playground_graphs_versions_activate_version",
        "post",
        ACTIVATE,
        response,
        request={"query": {"is_template": "true"}},
        note="Template writes answer like a missing graph (was 200, template activated).",
    )


def test_rs03_nc_create_with_foreign_node_is_a_declared_404(
    swagger,
    authenticated_client,
    graph,
    graph_version,
    node,
    template_graph,
    node_template,
):
    from agent_playground.models.node import Node

    other = GraphVersion.no_workspace_objects.create(
        graph=template_graph, version_number=1, status=GraphVersionStatus.DRAFT
    )
    outsider = Node.no_workspace_objects.create(
        graph_version=other,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Outsider",
        config={},
    )

    response = authenticated_client.post(
        _url(NCS, id=graph.id, version_id=graph_version.id),
        {
            "id": str(uuid.uuid4()),
            "source_node_id": str(node.id),
            "target_node_id": str(outsider.id),
        },
        format="json",
    )

    assert response.status_code == 404
    _check(
        swagger,
        "rs03_nc_create_foreign_node_404",
        "agent-playground_graphs_versions_node-connections_create",
        "post",
        NCS,
        response,
        request={"body": ["id", "source_node_id", "target_node_id(foreign)"]},
        note="A node outside the caller's scope reads like a missing node (was 400).",
    )


# ── R-A01: transition and deletion bodies ────────────────────────────────────


def test_ra01_version_create_full_matches_contract(
    swagger,
    authenticated_client,
    graph,
    graph_version,
    node_template,
    extensible_node_template,
):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    response = authenticated_client.post(
        _url(VERSIONS, id=graph.id),
        {
            "status": GraphVersionStatus.DRAFT,
            "commit_message": "full",
            "nodes": [
                {
                    "id": first,
                    "type": NodeType.ATOMIC,
                    "name": "A",
                    "node_template_id": str(node_template.id),
                    "config": {},
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": second,
                    "type": NodeType.ATOMIC,
                    "name": "B",
                    "node_template_id": str(extensible_node_template.id),
                    "config": {},
                    "position": {"x": 1, "y": 0},
                },
            ],
            "node_connections": [{"source_node_id": first, "target_node_id": second}],
        },
        format="json",
    )

    assert response.status_code == 201
    _check(
        swagger,
        "ra01_version_create_full_201",
        "agent-playground_graphs_versions_create",
        "post",
        VERSIONS,
        response,
        request={"body": ["status", "commit_message", "nodes[2]", "node_connections"]},
    )


@pytest.mark.parametrize("method", ["put", "patch"])
def test_ra01_version_metadata_update_matches_contract(
    swagger, authenticated_client, graph, graph_version, node, method
):
    response = getattr(authenticated_client, method)(
        _url(VERSION, id=graph.id, version_id=graph_version.id),
        {"commit_message": "meta only", "nodes": [], "node_connections": []},
        format="json",
    )

    assert response.status_code == 200
    _check(
        swagger,
        f"ra01_version_{method}_metadata_200",
        f"agent-playground_graphs_versions_{'update' if method == 'put' else 'partial_update'}",
        method,
        VERSION,
        response,
        request={
            "body": ["commit_message", "nodes(ignored)", "node_connections(ignored)"]
        },
        note="Content keys are ignored; the existing node is returned unchanged.",
    )


def test_ra01_version_commit_via_metadata_matches_contract(
    swagger,
    authenticated_client,
    graph,
    graph_version,
    active_graph_version,
    dynamic_node,
):
    response = authenticated_client.patch(
        _url(VERSION, id=graph.id, version_id=graph_version.id),
        {"status": GraphVersionStatus.ACTIVE, "commit_message": "ship it"},
        format="json",
    )

    assert response.status_code == 200
    _check(
        swagger,
        "ra01_version_patch_commit_200",
        "agent-playground_graphs_versions_partial_update",
        "patch",
        VERSION,
        response,
        request={"body": {"status": "active", "commit_message": "ship it"}},
    )


def test_ra01_activate_inactive_matches_contract(
    swagger,
    authenticated_client,
    graph,
    graph_version,
    active_graph_version,
    inactive_graph_version,
):
    response = authenticated_client.post(
        _url(ACTIVATE, id=graph.id, version_id=inactive_graph_version.id)
    )

    assert response.status_code == 200
    _check(
        swagger,
        "ra01_version_activate_inactive_200",
        "agent-playground_graphs_versions_activate_version",
        "post",
        ACTIVATE,
        response,
    )


def test_ra01_activate_draft_is_a_declared_400(
    swagger, authenticated_client, graph, graph_version
):
    response = authenticated_client.post(
        _url(ACTIVATE, id=graph.id, version_id=graph_version.id)
    )

    assert response.status_code == 400
    _check(
        swagger,
        "ra01_version_activate_draft_400",
        "agent-playground_graphs_versions_activate_version",
        "post",
        ACTIVATE,
        response,
    )


def test_ra01_delete_only_version_is_a_declared_400(
    swagger, authenticated_client, graph, graph_version
):
    response = authenticated_client.delete(
        _url(VERSION, id=graph.id, version_id=graph_version.id)
    )

    assert response.status_code == 400
    _check(
        swagger,
        "ra01_version_delete_only_400",
        "agent-playground_graphs_versions_delete",
        "delete",
        VERSION,
        response,
    )


def test_ra01_delete_active_version_matches_contract(
    swagger, authenticated_client, graph, graph_version, active_graph_version
):
    response = authenticated_client.delete(
        _url(VERSION, id=graph.id, version_id=active_graph_version.id)
    )

    assert response.status_code == 200
    _check(
        swagger,
        "ra01_version_delete_active_200",
        "agent-playground_graphs_versions_delete",
        "delete",
        VERSION,
        response,
        note="The graph is left with no active version; nothing is promoted.",
    )


# ── Graph deletes (TH-8413 targets) ──────────────────────────────────────────


def test_graph_delete_matches_contract(
    swagger, authenticated_client, graph, graph_version
):
    response = authenticated_client.delete(_url(GRAPH, id=graph.id))

    assert response.status_code == 200
    _check(
        swagger,
        "graph_delete_200",
        "agent-playground_graphs_delete",
        "delete",
        GRAPH,
        response,
    )


def test_graph_bulk_delete_matches_contract(
    swagger, authenticated_client, graph, graph_version
):
    response = authenticated_client.post(
        BULK_DELETE, {"ids": [str(graph.id)]}, format="json"
    )

    assert response.status_code == 200
    _check(
        swagger,
        "graph_bulk_delete_200",
        "agent-playground_graphs_bulk_delete",
        "post",
        BULK_DELETE,
        response,
        request={"body": ["ids[1]"]},
    )


def test_graph_bulk_delete_missing_is_a_declared_404(
    swagger, authenticated_client, graph, template_graph
):
    response = authenticated_client.post(
        BULK_DELETE + "?is_template=true",
        {"ids": [str(template_graph.id)]},
        format="json",
    )

    assert response.status_code == 404
    _check(
        swagger,
        "th8413_template_bulk_delete_404",
        "agent-playground_graphs_bulk_delete",
        "post",
        BULK_DELETE,
        response,
        request={"query": {"is_template": "true"}, "body": ["ids[template]"]},
        note="Template ids are reported as missing (was 200, template deleted).",
    )

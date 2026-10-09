"""TH-8216 B1 R-A01: legal Workbench version transitions and deletion.

Characterises every transition through the real endpoints and pins the
invariants: metadata PUT/PATCH never changes content, a draft is committed via
metadata, only inactive versions can be activated, the only version cannot be
deleted, and deleting the active version leaves no active version (no implicit
fallback).
"""

import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from agent_playground.models.choices import GraphVersionStatus, NodeType
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection

GRAPH = "/agent-playground/graphs/{g}/"
VERSIONS = "/agent-playground/graphs/{g}/versions/"
VERSION = "/agent-playground/graphs/{g}/versions/{v}/"
ACTIVATE = "/agent-playground/graphs/{g}/versions/{v}/activate/"
NODES = "/agent-playground/graphs/{g}/versions/{v}/nodes/"
NODE = "/agent-playground/graphs/{g}/versions/{v}/nodes/{n}/"


def _version_status_map(graph):
    return dict(
        GraphVersion.no_workspace_objects.filter(graph=graph).values_list(
            "id", "status"
        )
    )


# =============================================================================
# R-A01: legal version transitions and deletion
# =============================================================================


def _version_url(graph, version):
    return VERSION.format(g=graph.id, v=version.id)


def _node_ids(version):
    return sorted(
        Node.no_workspace_objects.filter(graph_version=version).values_list(
            "id", flat=True
        )
    )


class TestRA01Transitions:
    def test_create_full_version_as_draft(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        node_template,
        extensible_node_template,
    ):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        payload = {
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
        }

        response = authenticated_client.post(
            VERSIONS.format(g=graph.id), payload, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        result = response.json()["result"]
        assert result["version_number"] == 3
        assert result["status"] == GraphVersionStatus.DRAFT
        assert len(result["nodes"]) == 2
        assert len(result["node_connections"]) == 1
        active_graph_version.refresh_from_db()
        assert active_graph_version.status == GraphVersionStatus.ACTIVE

    def test_granular_edit_only_on_drafts(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        node_template,
        node_in_active_version,
    ):
        draft_nodes = NODES.format(g=graph.id, v=graph_version.id)
        node_id = str(uuid.uuid4())
        created = authenticated_client.post(
            draft_nodes,
            {
                "id": node_id,
                "type": "atomic",
                "name": "Draft Node",
                "node_template_id": str(node_template.id),
            },
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED
        node_url = NODE.format(g=graph.id, v=graph_version.id, n=node_id)
        patched = authenticated_client.patch(
            node_url, {"name": "Renamed"}, format="json"
        )
        assert patched.status_code == status.HTTP_200_OK
        assert authenticated_client.delete(node_url).status_code == status.HTTP_200_OK

        active_before = _node_ids(active_graph_version)
        rejected = [
            authenticated_client.post(
                NODES.format(g=graph.id, v=active_graph_version.id),
                {
                    "id": str(uuid.uuid4()),
                    "type": "atomic",
                    "name": "Nope",
                    "node_template_id": str(node_template.id),
                },
                format="json",
            ),
            authenticated_client.patch(
                NODE.format(
                    g=graph.id, v=active_graph_version.id, n=node_in_active_version.id
                ),
                {"name": "Nope"},
                format="json",
            ),
            authenticated_client.delete(
                NODE.format(
                    g=graph.id, v=active_graph_version.id, n=node_in_active_version.id
                )
            ),
        ]
        for response in rejected:
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.json()["message"] == "Can only update draft versions."
        assert _node_ids(active_graph_version) == active_before
        node_in_active_version.refresh_from_db()
        assert node_in_active_version.name != "Nope"

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_metadata_update_ignores_content_fields(
        self,
        authenticated_client,
        graph,
        graph_version,
        node,
        node_template,
        method,
    ):
        """PUT/PATCH is metadata-only: content keys are ignored, never applied."""
        before = _node_ids(graph_version)
        payload = {
            "commit_message": "meta only",
            "nodes": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "atomic",
                    "name": "Smuggled",
                    "node_template_id": str(node_template.id),
                }
            ],
            "node_connections": [
                {"source_node_id": str(node.id), "target_node_id": str(node.id)}
            ],
        }

        response = getattr(authenticated_client, method)(
            _version_url(graph, graph_version), payload, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["commit_message"] == "meta only"
        assert result["status"] == GraphVersionStatus.DRAFT
        assert [n["id"] for n in result["nodes"]] == [str(node.id)]
        assert _node_ids(graph_version) == before
        assert not NodeConnection.no_workspace_objects.filter(
            graph_version=graph_version
        ).exists()

    @pytest.mark.parametrize(
        "version_fixture", ["active_graph_version", "inactive_graph_version"]
    )
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_metadata_update_rejects_committed_versions(
        self, request, authenticated_client, graph, version_fixture, method
    ):
        version = request.getfixturevalue(version_fixture)
        before = (version.status, version.commit_message)

        response = getattr(authenticated_client, method)(
            _version_url(graph, version), {"commit_message": "rewrite"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["message"] == "Can only update draft versions."
        version.refresh_from_db()
        assert (version.status, version.commit_message) == before

    def test_commit_draft_via_metadata(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        dynamic_node,
    ):
        response = authenticated_client.patch(
            _version_url(graph, graph_version),
            {"status": GraphVersionStatus.ACTIVE, "commit_message": "ship it"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["status"] == GraphVersionStatus.ACTIVE
        assert response.json()["result"]["commit_message"] == "ship it"
        assert _version_status_map(graph) == {
            graph_version.id: GraphVersionStatus.ACTIVE,
            active_graph_version.id: GraphVersionStatus.INACTIVE,
        }

    def test_metadata_update_rejects_inactive_status(
        self, authenticated_client, graph, graph_version
    ):
        response = authenticated_client.patch(
            _version_url(graph, graph_version),
            {"status": GraphVersionStatus.INACTIVE},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        graph_version.refresh_from_db()
        assert graph_version.status == GraphVersionStatus.DRAFT

    def test_activate_inactive_version(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        inactive_graph_version,
    ):
        response = authenticated_client.post(
            ACTIVATE.format(g=graph.id, v=inactive_graph_version.id)
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["status"] == GraphVersionStatus.ACTIVE
        assert _version_status_map(graph) == {
            graph_version.id: GraphVersionStatus.DRAFT,
            active_graph_version.id: GraphVersionStatus.INACTIVE,
            inactive_graph_version.id: GraphVersionStatus.ACTIVE,
        }

    @pytest.mark.parametrize("target", ["graph_version", "active_graph_version"])
    def test_activate_rejects_draft_and_active(
        self,
        request,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        target,
    ):
        version = request.getfixturevalue(target)
        before = _version_status_map(graph)

        response = authenticated_client.post(ACTIVATE.format(g=graph.id, v=version.id))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["message"] == "Only inactive versions can be activated."
        assert _version_status_map(graph) == before

    def test_delete_only_version_rejected(
        self, authenticated_client, graph, graph_version, node
    ):
        response = authenticated_client.delete(_version_url(graph, graph_version))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["message"] == "Cannot delete the only version."
        assert GraphVersion.no_workspace_objects.filter(pk=graph_version.pk).exists()
        assert _node_ids(graph_version) == [node.id]

    def test_delete_only_live_version_rejected_after_sibling_deleted(
        self, authenticated_client, graph, graph_version, active_graph_version
    ):
        first = authenticated_client.delete(_version_url(graph, active_graph_version))
        assert first.status_code == status.HTTP_200_OK

        second = authenticated_client.delete(_version_url(graph, graph_version))

        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert GraphVersion.no_workspace_objects.filter(pk=graph_version.pk).exists()

    def test_delete_active_version_leaves_no_active_version_in_storage(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        inactive_graph_version,
    ):
        """R-A01 in storage: no version is active and nothing is promoted."""
        response = authenticated_client.delete(
            _version_url(graph, active_graph_version)
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {"message": "Version deleted successfully"}
        assert _version_status_map(graph) == {
            graph_version.id: GraphVersionStatus.DRAFT,
            inactive_graph_version.id: GraphVersionStatus.INACTIVE,
        }

    def test_characterization_o2_read_surface_after_active_delete(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        inactive_graph_version,
    ):
        """CHARACTERIZATION, not an invariant: owner decision O2 may change it.

        R-A01 holds in storage; the read naming is owner decision O2.
        ``active_version`` (detail) and
        ``active_version_id`` (list) mean "latest version of any status", so
        after the active version is deleted they name the latest remaining
        (inactive) version, which reads like an implicit fallback.
        """
        authenticated_client.delete(_version_url(graph, active_graph_version))

        detail = authenticated_client.get(GRAPH.format(g=graph.id)).json()["result"]
        assert detail["active_version"]["id"] == str(inactive_graph_version.id)
        assert detail["active_version"]["status"] == GraphVersionStatus.INACTIVE

        listed = authenticated_client.get(reverse("graph-list")).json()["result"]
        row = next(r for r in listed["graphs"] if r["id"] == str(graph.id))
        assert row["active_version_id"] == str(inactive_graph_version.id)


@pytest.fixture
def inactive_graph_version(db, graph):
    return GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=3,
        status=GraphVersionStatus.INACTIVE,
        tags=[],
    )

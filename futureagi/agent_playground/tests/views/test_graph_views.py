"""Comprehensive tests for GraphViewSet."""

import uuid

from django.urls import reverse
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from agent_playground.models.choices import GraphVersionStatus, NodeType, PortDirection
from agent_playground.models.edge import Edge
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.port import Port

# Note: api_client and authenticated_client fixtures are inherited from
# agent_playground/tests/conftest.py with proper workspace injection

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


# ==================== Graph List Tests ====================


class TestGraphList:
    """Tests for GET /agent-playground/graphs/"""

    def test_list_returns_user_graphs(self, authenticated_client, graph):
        """Test that list returns graphs for the user's organization."""
        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert len(response.data["result"]["graphs"]) == 1
        assert response.data["result"]["graphs"][0]["id"] == str(graph.id)

    def test_list_returns_lightweight_fields(self, authenticated_client, graph):
        """Test that list returns only lightweight fields."""
        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_data = response.data["result"]["graphs"][0]

        # Should have lightweight fields
        assert "id" in graph_data
        assert "name" in graph_data
        assert "description" in graph_data
        assert "is_template" in graph_data
        assert "created_at" in graph_data
        assert "created_by" in graph_data
        assert "active_version_number" in graph_data
        assert "node_count" in graph_data

        # Should NOT have detail fields
        assert "active_version" not in graph_data

    def test_list_excludes_other_org_graphs(
        self, authenticated_client, graph, organization
    ):
        """Test that list excludes graphs from other organizations."""
        # Create another org and graph
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@test.com",
            password="testpass",
            name="Other User",
            organization=other_org,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=other_user, organization=other_org, defaults={"is_active": True}
        )
        other_graph = Graph.no_workspace_objects.create(
            organization=other_org,
            name="Other Graph",
            created_by=other_user,
        )

        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_ids = [g["id"] for g in response.data["result"]["graphs"]]
        assert str(graph.id) in graph_ids
        assert str(other_graph.id) not in graph_ids

    def test_list_excludes_deleted_graphs(
        self, authenticated_client, graph, organization, user
    ):
        """Test that list excludes soft-deleted graphs."""
        deleted_graph = Graph.no_workspace_objects.create(
            organization=organization,
            name="Deleted Graph",
            created_by=user,
        )
        deleted_graph.delete()  # Soft delete

        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_ids = [g["id"] for g in response.data["result"]["graphs"]]
        assert str(deleted_graph.id) not in graph_ids

    def test_list_requires_authentication(self, api_client, graph):
        """Test that list requires authentication."""
        url = reverse("graph-list")
        response = api_client.get(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_returns_empty_for_new_org(self, api_client, organization, workspace):
        """Test that list returns empty for org with no graphs."""
        new_user = User.objects.create_user(
            email="newuser@test.com",
            password="testpass",
            name="New User",
            organization=organization,
        )
        api_client.force_authenticate(user=new_user)
        api_client.set_workspace(workspace)

        url = reverse("graph-list")
        response = api_client.get(url)

        api_client.stop_workspace_injection()

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["graphs"] == []

    def test_list_ordered_by_created_at_desc(
        self, authenticated_client, organization, user, workspace
    ):
        """Test that graphs are ordered by created_at descending."""
        # Create multiple graphs
        graph1 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 1",
            created_by=user,
        )
        graph2 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 2",
            created_by=user,
        )
        graph3 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 3",
            created_by=user,
        )

        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graphs = response.data["result"]["graphs"]
        # Most recent first
        assert graphs[0]["id"] == str(graph3.id)
        assert graphs[1]["id"] == str(graph2.id)
        assert graphs[2]["id"] == str(graph1.id)


# ==================== Graph Create Tests ====================


class TestGraphCreate:
    """Tests for POST /agent-playground/graphs/"""

    def test_create_graph_with_draft_version(self, authenticated_client, user):
        """Test that creating a graph also creates an empty draft version."""
        url = reverse("graph-list")
        data = {
            "name": "New Graph",
            "description": "A new graph description",
        }
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] is True
        assert response.data["result"]["name"] == "New Graph"
        assert response.data["result"]["description"] == "A new graph description"

        # Should have an active_version (the draft)
        active_version = response.data["result"]["active_version"]
        assert active_version is not None
        assert active_version["version_number"] == 1
        assert active_version["status"] == GraphVersionStatus.DRAFT
        assert active_version["nodes"] == []

    def test_create_graph_without_description(self, authenticated_client):
        """Test creating a graph without description."""
        url = reverse("graph-list")
        data = {"name": "New Graph"}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["description"] is None

    def test_create_graph_with_empty_description(self, authenticated_client):
        """Test creating a graph with empty description."""
        url = reverse("graph-list")
        data = {"name": "New Graph", "description": ""}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["description"] == ""

    def test_create_graph_requires_name(self, authenticated_client):
        """Test that name is required."""
        url = reverse("graph-list")
        data = {"description": "A description without name"}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_graph_empty_name_fails(self, authenticated_client):
        """Test that empty name fails."""
        url = reverse("graph-list")
        data = {"name": ""}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_requires_authentication(self, api_client):
        """Test that create requires authentication."""
        url = reverse("graph-list")
        data = {"name": "New Graph"}
        response = api_client.post(url, data=data, format="json")

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_sets_created_by(self, authenticated_client, user):
        """Test that created_by is set to the current user."""
        url = reverse("graph-list")
        data = {"name": "New Graph"}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["created_by"]["id"] == str(user.id)

    def test_create_adds_user_to_collaborators(self, authenticated_client, user):
        """Test that creator is added to collaborators."""
        url = reverse("graph-list")
        data = {"name": "New Graph"}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        collaborator_ids = [c["id"] for c in response.data["result"]["collaborators"]]
        assert str(user.id) in collaborator_ids

    def test_create_sets_is_template_false(self, authenticated_client):
        """Test that is_template is set to False by default."""
        url = reverse("graph-list")
        data = {"name": "New Graph"}
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["is_template"] is False


# ==================== Graph Retrieve Tests ====================


class TestGraphRetrieve:
    """Tests for GET /agent-playground/graphs/{id}/"""

    def test_retrieve_returns_full_details(
        self, authenticated_client, graph, active_graph_version
    ):
        """Test that retrieve returns full graph details with active version."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True

        graph_data = response.data["result"]
        assert graph_data["id"] == str(graph.id)
        assert graph_data["name"] == graph.name
        assert "active_version" in graph_data
        assert graph_data["active_version"]["id"] == str(active_graph_version.id)

    def test_retrieve_returns_draft_when_no_active(
        self, authenticated_client, graph, graph_version
    ):
        """Test that retrieve returns latest draft when no active version."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert (
            response.data["result"]["active_version"]["status"]
            == GraphVersionStatus.DRAFT
        )

    def test_retrieve_returns_latest_draft(self, authenticated_client, graph):
        """Test that retrieve returns the latest draft when multiple exist."""
        # Create multiple drafts
        GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=1, status=GraphVersionStatus.DRAFT
        )
        draft2 = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=2, status=GraphVersionStatus.DRAFT
        )

        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["active_version"]["id"] == str(draft2.id)

    def test_retrieve_includes_nested_nodes_and_edges(
        self, authenticated_client, graph, active_graph_version, node_in_active_version
    ):
        """Test that retrieve includes nested nodes and edges."""
        # Create a port on the node
        Port.no_workspace_objects.create(
            node=node_in_active_version,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        version = response.data["result"]["active_version"]
        assert len(version["nodes"]) == 1
        assert len(version["nodes"][0]["ports"]) == 1

    def test_retrieve_includes_input_mappings_for_subgraph(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_template,
        referenced_graph,
        active_referenced_graph_version,
    ):
        """Test that Get Graph response includes correct input_mappings for subgraph nodes."""
        # Create a source node with an output port
        source_node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="DataLoader",
            config={},
            position={"x": 0, "y": 0},
        )
        source_output = Port.no_workspace_objects.create(
            node=source_node,
            key="output1",
            display_name="output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Create a subgraph node with two input ports (one mapped, one unmapped)
        subgraph_node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="SubgraphNode",
            config={},
            position={"x": 200, "y": 0},
        )
        mapped_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="context",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        mapped_port.save(skip_validation=True)
        unmapped_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="question",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        unmapped_port.save(skip_validation=True)

        # Create NodeConnection + Edge for the mapped port
        NodeConnection.no_workspace_objects.create(
            graph_version=active_graph_version,
            source_node=source_node,
            target_node=subgraph_node,
        )
        Edge.no_workspace_objects.create(
            graph_version=active_graph_version,
            source_port=source_output,
            target_port=mapped_port,
        )

        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        version_data = response.data["result"]["active_version"]
        subgraph_data = next(
            n for n in version_data["nodes"] if n["name"] == "SubgraphNode"
        )
        # Convert list to dict for easier testing
        mappings = {m["key"]: m["value"] for m in subgraph_data["input_mappings"]}
        assert mappings == {
            "context": "DataLoader.output",
            "question": None,
        }

        # Atomic node should have input_mappings = None
        source_data = next(
            n for n in version_data["nodes"] if n["name"] == "DataLoader"
        )
        assert source_data["input_mappings"] is None

    def test_retrieve_requires_authentication(self, api_client, graph):
        """Test that retrieve requires authentication."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = api_client.get(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_retrieve_nonexistent_returns_404(self, authenticated_client):
        """Test that retrieve returns 404 for nonexistent graph."""
        url = reverse("graph-detail", kwargs={"pk": str(uuid.uuid4())})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_other_org_graph_returns_404(self, authenticated_client):
        """Test that retrieving another org's graph returns 404."""
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other@test.com",
            password="testpass",
            name="Other User",
            organization=other_org,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=other_user, organization=other_org, defaults={"is_active": True}
        )
        other_graph = Graph.no_workspace_objects.create(
            organization=other_org, name="Other Graph", created_by=other_user
        )

        url = reverse("graph-detail", kwargs={"pk": str(other_graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==================== Graph Update Tests ====================


class TestGraphPartialUpdate:
    """Tests for PATCH /agent-playground/graphs/{id}/"""

    def test_update_name(self, authenticated_client, graph):
        """Test updating graph name."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        data = {"name": "Updated Name"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["name"] == "Updated Name"

        # Verify in database
        graph.refresh_from_db()
        assert graph.name == "Updated Name"

    def test_update_description(self, authenticated_client, graph):
        """Test updating graph description."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        data = {"description": "Updated description"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["description"] == "Updated description"

    def test_update_both_fields(self, authenticated_client, graph):
        """Test updating both name and description."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        data = {"name": "New Name", "description": "New Description"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["name"] == "New Name"
        assert response.data["result"]["description"] == "New Description"

    def test_update_to_null_description(self, authenticated_client, graph):
        """Test updating description to null."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        data = {"description": None}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["description"] is None

    def test_update_does_not_touch_versions(
        self, authenticated_client, graph, graph_version, node, input_port
    ):
        """Test that update does not modify versions."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        data = {"name": "Updated Name"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK

        # Version should still have its nodes
        graph_version.refresh_from_db()
        assert (
            Node.no_workspace_objects.filter(graph_version=graph_version).count() == 1
        )

    def test_update_requires_authentication(self, api_client, graph):
        """Test that update requires authentication."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        data = {"name": "Updated Name"}
        response = api_client.patch(url, data=data, format="json")

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


class TestGraphDirectDetailRoutes:
    """Tests for PUT/DELETE /agent-playground/graphs/{id}/"""

    def test_put_updates_graph_metadata(self, authenticated_client, graph):
        """Test that router-level PUT supports metadata updates."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.put(
            url,
            data={"name": "Updated via PUT", "description": "PUT description"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["name"] == "Updated via PUT"
        assert response.data["result"]["description"] == "PUT description"

        graph.refresh_from_db()
        assert graph.name == "Updated via PUT"
        assert graph.description == "PUT description"

    def test_put_other_workspace_graph_returns_404(
        self, authenticated_client, organization, user
    ):
        """Test that router-level PUT remains workspace scoped."""
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_workspace_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=other_workspace,
            name="Other Workspace Graph",
            description="Should stay unchanged",
            created_by=user,
        )

        url = reverse("graph-detail", kwargs={"pk": str(other_workspace_graph.id)})
        response = authenticated_client.put(
            url,
            data={"name": "Leaked Update", "description": "Leaked"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        other_workspace_graph.refresh_from_db()
        assert other_workspace_graph.name == "Other Workspace Graph"
        assert other_workspace_graph.description == "Should stay unchanged"

    def test_delete_cascades_graph_content(
        self,
        authenticated_client,
        graph,
        graph_version,
        node,
        input_port,
        output_port,
        second_node,
        second_node_input_port,
        node_connection,
        edge,
        dataset,
        graph_dataset,
    ):
        """Test that router-level DELETE uses graph cascade soft-delete."""
        url = reverse("graph-detail", kwargs={"pk": str(graph.id)})
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["message"] == "Graph deleted successfully"

        for instance in [
            graph,
            graph_version,
            node,
            input_port,
            output_port,
            second_node,
            second_node_input_port,
            node_connection,
            edge,
            dataset,
            graph_dataset,
        ]:
            instance.refresh_from_db()
            assert instance.deleted is True

    def test_delete_blocked_by_reference(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
        graph_version,
    ):
        """Test that router-level DELETE blocks external graph references."""
        Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Ref",
            ref_graph_version=active_referenced_graph_version,
            config={},
        )

        url = reverse("graph-detail", kwargs={"pk": str(referenced_graph.id)})
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert referenced_graph.name in response.data["result"]
        assert graph.name in response.data["result"]

        referenced_graph.refresh_from_db()
        assert referenced_graph.deleted is False


# ==================== Graph Bulk Delete Tests ====================


class TestGraphBulkDelete:
    """Tests for POST /agent-playground/graphs/delete/"""

    def test_bulk_delete_soft_deletes_graph(self, authenticated_client, graph):
        """Test that bulk delete soft-deletes the graph."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"ids": [str(graph.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["message"] == "Graphs deleted successfully"

        # Verify soft-deleted
        graph.refresh_from_db()
        assert graph.deleted is True

    def test_bulk_delete_cascades_to_versions(
        self, authenticated_client, graph, graph_version
    ):
        """Test that bulk delete cascades soft-delete to versions."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"ids": [str(graph.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        graph_version.refresh_from_db()
        assert graph_version.deleted is True

    def test_bulk_delete_cascades_to_nodes(
        self, authenticated_client, graph, graph_version, node
    ):
        """Test that bulk delete cascades soft-delete to nodes."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"ids": [str(graph.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        node.refresh_from_db()
        assert node.deleted is True

    def test_bulk_delete_cascades_to_ports(
        self, authenticated_client, graph, graph_version, node, input_port
    ):
        """Test that bulk delete cascades soft-delete to ports."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"ids": [str(graph.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        input_port.refresh_from_db()
        assert input_port.deleted is True

    def test_bulk_delete_cascades_to_edges(
        self,
        authenticated_client,
        graph,
        graph_version,
        output_port,
        second_node_input_port,
        edge,
    ):
        """Test that bulk delete cascades soft-delete to edges."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"ids": [str(graph.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        edge.refresh_from_db()
        assert edge.deleted is True

    def test_bulk_delete_requires_authentication(self, api_client, graph):
        """Test that bulk delete requires authentication."""
        url = reverse("graph-bulk-delete")
        response = api_client.post(url, data={"ids": [str(graph.id)]}, format="json")

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_bulk_delete_multiple_graphs(
        self, authenticated_client, organization, workspace, user
    ):
        """Test deleting multiple graphs at once."""
        graph1 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 1",
            created_by=user,
        )
        graph2 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 2",
            created_by=user,
        )
        graph3 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 3",
            created_by=user,
        )

        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"ids": [str(graph1.id), str(graph2.id), str(graph3.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        for g in [graph1, graph2, graph3]:
            g.refresh_from_db()
            assert g.deleted is True

    def test_bulk_delete_blocked_by_reference(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
        graph_version,
    ):
        """Test that deleting a graph is blocked when its version is referenced by another graph not in the list."""
        # Create a subgraph node in graph that references referenced_graph's active version
        Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Ref",
            ref_graph_version=active_referenced_graph_version,
            config={},
        )

        # Try to delete only the referenced_graph (not the graph that references it)
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"ids": [str(referenced_graph.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert referenced_graph.name in response.data["result"]
        assert graph.name in response.data["result"]

    def test_bulk_delete_allowed_when_referencing_graph_also_deleted(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
        graph_version,
    ):
        """Test that deletion is allowed when the referencing graph is also in the deletion set."""
        # Create a subgraph node in graph that references referenced_graph's active version
        Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Ref",
            ref_graph_version=active_referenced_graph_version,
            config={},
        )

        # Delete both graphs together — should succeed
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"ids": [str(graph.id), str(referenced_graph.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        graph.refresh_from_db()
        referenced_graph.refresh_from_db()
        assert graph.deleted is True
        assert referenced_graph.deleted is True

    def test_bulk_delete_nonexistent_id_returns_404(self, authenticated_client):
        """Test that a nonexistent graph ID returns 404."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"ids": [str(uuid.uuid4())]},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "missing_ids" in response.data["result"]

    def test_bulk_delete_empty_list_returns_400(self, authenticated_client):
        """Test that an empty list returns 400."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"ids": []},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bulk_delete_select_all(
        self, authenticated_client, organization, workspace, user
    ):
        """Test that select_all deletes all graphs in the org."""
        graph1 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 1",
            created_by=user,
        )
        graph2 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 2",
            created_by=user,
        )

        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"select_all": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["message"] == "Graphs deleted successfully"

        graph1.refresh_from_db()
        graph2.refresh_from_db()
        assert graph1.deleted is True
        assert graph2.deleted is True

    def test_bulk_delete_select_all_with_exclude_ids(
        self, authenticated_client, organization, workspace, user
    ):
        """Test that select_all with exclude_ids keeps excluded graphs."""
        graph1 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 1",
            created_by=user,
        )
        graph2 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 2",
            created_by=user,
        )
        graph3 = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph 3",
            created_by=user,
        )

        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"select_all": True, "exclude_ids": [str(graph2.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        graph1.refresh_from_db()
        graph2.refresh_from_db()
        graph3.refresh_from_db()
        assert graph1.deleted is True
        assert graph2.deleted is False
        assert graph3.deleted is True

    def test_bulk_delete_select_all_and_ids_returns_400(self, authenticated_client):
        """Test that providing both select_all and ids returns 400."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"select_all": True, "ids": [str(uuid.uuid4())]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bulk_delete_neither_select_all_nor_ids_returns_400(
        self, authenticated_client
    ):
        """Test that providing neither select_all nor ids returns 400."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bulk_delete_exclude_ids_without_select_all_returns_400(
        self, authenticated_client
    ):
        """Test that providing exclude_ids without select_all returns 400."""
        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url,
            data={"ids": [str(uuid.uuid4())], "exclude_ids": [str(uuid.uuid4())]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_bulk_delete_select_all_does_not_affect_other_org(
        self, authenticated_client, organization, workspace, user
    ):
        """Test that select_all only deletes graphs in the user's org."""
        # Create a graph in the authenticated user's org
        own_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Own Graph",
            created_by=user,
        )

        # Create a graph in a different org
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="other_del@test.com",
            password="testpass",
            name="Other User",
            organization=other_org,
        )
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=other_user, organization=other_org, defaults={"is_active": True}
        )
        other_graph = Graph.no_workspace_objects.create(
            organization=other_org,
            name="Other Org Graph",
            created_by=other_user,
        )

        url = reverse("graph-bulk-delete")
        response = authenticated_client.post(
            url, data={"select_all": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

        own_graph.refresh_from_db()
        other_graph.refresh_from_db()
        assert own_graph.deleted is True
        assert other_graph.deleted is False


# ==================== Version List Tests ====================


class TestGraphListVersions:
    """Tests for GET /agent-playground/graphs/{id}/versions/"""

    def test_list_versions_returns_all_versions(
        self, authenticated_client, graph, graph_version, active_graph_version
    ):
        """Test that list_versions returns all versions for a graph."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert len(response.data["result"]["versions"]) == 2

    def test_list_versions_returns_lightweight_fields(
        self, authenticated_client, graph, graph_version
    ):
        """Test that list_versions returns only lightweight fields."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        version_data = response.data["result"]["versions"][0]

        assert "id" in version_data
        assert "version_number" in version_data
        assert "status" in version_data
        assert "commit_message" in version_data
        assert "created_at" in version_data

        # Should NOT have nested data
        assert "nodes" not in version_data
        assert "edges" not in version_data

    def test_list_versions_ordered_by_version_number_desc(
        self, authenticated_client, graph
    ):
        """Test that versions are ordered by version_number descending."""
        GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=1, status=GraphVersionStatus.INACTIVE
        )
        GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=2, status=GraphVersionStatus.INACTIVE
        )
        GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=3, status=GraphVersionStatus.ACTIVE
        )

        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        versions = response.data["result"]["versions"]
        assert versions[0]["version_number"] == 3
        assert versions[1]["version_number"] == 2
        assert versions[2]["version_number"] == 1

    def test_list_versions_includes_pagination_metadata(
        self, authenticated_client, graph, graph_version
    ):
        """Test that list versions response includes pagination metadata fields."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        metadata = response.data["result"]["metadata"]
        assert "total_count" in metadata
        assert "page_number" in metadata
        assert "page_size" in metadata
        assert "total_pages" in metadata
        assert metadata["total_count"] == 1

    def test_list_versions_pagination_with_page_size(self, authenticated_client, graph):
        """Test pagination with custom page_size."""
        for i in range(1, 4):
            GraphVersion.no_workspace_objects.create(
                graph=graph, version_number=i, status=GraphVersionStatus.DRAFT
            )

        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url, {"page_size": 2})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["result"]["versions"]) == 2
        metadata = response.data["result"]["metadata"]
        assert metadata["total_count"] == 3
        assert metadata["page_size"] == 2
        assert metadata["total_pages"] == 2

    def test_list_versions_pagination_second_page(self, authenticated_client, graph):
        """Test that second page returns the remainder."""
        for i in range(1, 4):
            GraphVersion.no_workspace_objects.create(
                graph=graph, version_number=i, status=GraphVersionStatus.DRAFT
            )

        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url, {"page_size": 2, "page_number": 2})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["result"]["versions"]) == 1
        metadata = response.data["result"]["metadata"]
        assert metadata["total_count"] == 3
        assert metadata["page_number"] == 2


# ==================== Version Retrieve Tests ====================


class TestGraphRetrieveVersion:
    """Tests for GET /agent-playground/graphs/{id}/versions/{version_id}/"""

    def test_retrieve_version_returns_full_details(
        self, authenticated_client, graph, graph_version, node, input_port, output_port
    ):
        """Test that retrieve_version returns full version details with nodes and edges."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True

        version_data = response.data["result"]
        assert version_data["id"] == str(graph_version.id)
        assert "nodes" in version_data
        assert len(version_data["nodes"]) == 1

    def test_retrieve_version_includes_input_mappings_for_subgraph(
        self,
        authenticated_client,
        graph,
        graph_version,
        node_template,
        referenced_graph,
        active_referenced_graph_version,
    ):
        """Test that Get Graph Version includes correct input_mappings for subgraph nodes."""
        # Create a source node with an output port
        source_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="DataLoader",
            config={},
            position={"x": 0, "y": 0},
        )
        source_output = Port.no_workspace_objects.create(
            node=source_node,
            key="output1",
            display_name="output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Create a subgraph node with two input ports (one mapped, one unmapped)
        subgraph_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="SubgraphNode",
            config={},
            position={"x": 200, "y": 0},
        )
        mapped_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="context",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        mapped_port.save(skip_validation=True)
        unmapped_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="question",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        unmapped_port.save(skip_validation=True)

        # Create NodeConnection + Edge for the mapped port
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=source_node,
            target_node=subgraph_node,
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=source_output,
            target_port=mapped_port,
        )

        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        version_data = response.data["result"]
        subgraph_data = next(
            n for n in version_data["nodes"] if n["name"] == "SubgraphNode"
        )
        # Convert list to dict for easier testing
        mappings = {m["key"]: m["value"] for m in subgraph_data["input_mappings"]}
        assert mappings == {
            "context": "DataLoader.output",
            "question": None,
        }

        # Atomic node should have input_mappings = None
        source_data = next(
            n for n in version_data["nodes"] if n["name"] == "DataLoader"
        )
        assert source_data["input_mappings"] is None

    def test_retrieve_nonexistent_version_returns_404(
        self, authenticated_client, graph
    ):
        """Test that retrieve_version returns 404 for nonexistent version."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(uuid.uuid4())},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==================== Version Create Tests ====================


class TestGraphCreateVersion:
    """Tests for POST /agent-playground/graphs/{id}/versions/create"""

    def test_create_version_increments_version_number(
        self, authenticated_client, graph, graph_version
    ):
        """Test that create_version creates a new draft with incremented version number."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.post(url, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] is True
        assert response.data["result"]["version_number"] == 2
        assert response.data["result"]["status"] == GraphVersionStatus.DRAFT

    def test_create_version_starts_empty(
        self, authenticated_client, graph, graph_version
    ):
        """Test that new version starts with no nodes or edges."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.post(url, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["nodes"] == []

    def test_create_version_on_graph_with_no_versions(
        self, authenticated_client, organization, user, workspace
    ):
        """Test creating first version on a graph with no versions."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Empty Graph",
            created_by=user,
        )

        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = authenticated_client.post(url, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["version_number"] == 1


# ==================== Version Update Tests ====================


class TestGraphUpdateVersion:
    """Tests for PUT/PATCH /agent-playground/graphs/{id}/versions/{version_id}/

    update_version is now metadata-only (status promotion + commit_message).
    """

    def test_update_commit_message(self, authenticated_client, graph, graph_version):
        """Test updating commit_message on a draft version."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        data = {"commit_message": "Work in progress"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["commit_message"] == "Work in progress"

    def test_publish_version(
        self, authenticated_client, graph, graph_version, dynamic_node_template
    ):
        """Test publishing a draft version to active via PATCH."""
        # Pre-populate content so the version has a node
        Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=dynamic_node_template,
            type=NodeType.ATOMIC,
            name="Node 1",
            config={},
            position={"x": 100, "y": 100},
        )

        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        data = {
            "status": GraphVersionStatus.ACTIVE,
            "commit_message": "Initial release",
        }
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["status"] == GraphVersionStatus.ACTIVE
        assert response.data["result"]["commit_message"] == "Initial release"

    def test_publish_deactivates_previous_active(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        dynamic_node_template,
    ):
        """Test that publishing a version deactivates the previous active version."""
        assert active_graph_version.status == GraphVersionStatus.ACTIVE

        # Pre-populate content
        Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=dynamic_node_template,
            type=NodeType.ATOMIC,
            name="Node 1",
            config={},
            position={"x": 100, "y": 100},
        )

        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        data = {
            "status": GraphVersionStatus.ACTIVE,
            "commit_message": "New release",
        }
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK

        # Verify old active is now inactive
        active_graph_version.refresh_from_db()
        assert active_graph_version.status == GraphVersionStatus.INACTIVE

    def test_cannot_update_active_version(
        self, authenticated_client, graph, active_graph_version
    ):
        """Test that active versions cannot be updated."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(active_graph_version.id)},
        )
        data = {"commit_message": "nope"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_also_works_as_metadata_update(
        self, authenticated_client, graph, graph_version
    ):
        """Test that PUT also works for metadata-only update."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        data = {"commit_message": "via PUT"}
        response = authenticated_client.put(url, data=data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["commit_message"] == "via PUT"


# ==================== Referenceable Graphs Tests ====================


class TestGraphReferenceableGraphs:
    """Tests for GET /agent-playground/graphs/{id}/referenceable-graphs/"""

    def test_returns_graphs_with_active_versions(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
    ):
        """Test that referenceable-graphs returns graphs with active versions."""
        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True

        graphs = response.data["result"]["graphs"]
        assert len(graphs) == 1
        assert graphs[0]["id"] == str(referenced_graph.id)

        # Check versions array structure
        assert "versions" in graphs[0]
        assert len(graphs[0]["versions"]) == 1
        version = graphs[0]["versions"][0]
        assert version["id"] == str(active_referenced_graph_version.id)
        assert (
            version["version_number"] == active_referenced_graph_version.version_number
        )
        assert version["status"] == GraphVersionStatus.ACTIVE

    def test_excludes_self(self, authenticated_client, graph, active_graph_version):
        """Test that referenceable-graphs excludes the current graph."""
        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_ids = [g["id"] for g in response.data["result"]["graphs"]]
        assert str(graph.id) not in graph_ids

    def test_excludes_graphs_with_only_draft_versions(
        self, authenticated_client, graph, referenced_graph, referenced_graph_version
    ):
        """Test that graphs with only draft versions are excluded."""
        # referenced_graph_version is DRAFT by default
        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_ids = [g["id"] for g in response.data["result"]["graphs"]]
        assert str(referenced_graph.id) not in graph_ids  # Only DRAFT, excluded

    def test_includes_inactive_versions(
        self, authenticated_client, graph, referenced_graph
    ):
        """Test that inactive versions are included in referenceable graphs."""
        # Create active version
        active_version = GraphVersion.no_workspace_objects.create(
            graph=referenced_graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )

        # Create inactive version
        inactive_version = GraphVersion.no_workspace_objects.create(
            graph=referenced_graph,
            version_number=2,
            status=GraphVersionStatus.INACTIVE,
        )

        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graphs = response.data["result"]["graphs"]

        # Find the referenced graph
        ref_graph = next(
            (g for g in graphs if g["id"] == str(referenced_graph.id)), None
        )
        assert ref_graph is not None

        # Should have both versions
        assert len(ref_graph["versions"]) == 2
        version_ids = [v["id"] for v in ref_graph["versions"]]
        assert str(active_version.id) in version_ids
        assert str(inactive_version.id) in version_ids

        # Check statuses
        statuses = [v["status"] for v in ref_graph["versions"]]
        assert GraphVersionStatus.ACTIVE in statuses
        assert GraphVersionStatus.INACTIVE in statuses

    def test_referenceable_graphs_response_structure(
        self, authenticated_client, graph, referenced_graph
    ):
        """Test the response structure includes versions array with exposed ports."""
        # Create active version with ports
        GraphVersion.no_workspace_objects.create(
            graph=referenced_graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )

        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graphs = response.data["result"]["graphs"]

        # Verify structure
        for graph_data in graphs:
            assert "id" in graph_data
            assert "name" in graph_data
            assert "description" in graph_data
            assert "is_template" in graph_data
            assert "versions" in graph_data
            assert isinstance(graph_data["versions"], list)

            # Verify version structure
            for version in graph_data["versions"]:
                assert "id" in version
                assert "version_number" in version
                assert "status" in version
                assert "exposed_ports" in version
                assert isinstance(version["exposed_ports"], list)

    def test_excludes_graphs_that_would_create_cycle(
        self,
        authenticated_client,
        graph,
        graph_version,
        referenced_graph,
        active_referenced_graph_version,
    ):
        """Test that graphs that would create a cycle are excluded."""
        # Create a subgraph node in referenced_graph that points to graph's active version
        active_graph_version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=3, status=GraphVersionStatus.ACTIVE
        )

        # Add a node in referenced_graph that references graph
        ref_version_draft = GraphVersion.no_workspace_objects.create(
            graph=referenced_graph, version_number=3, status=GraphVersionStatus.DRAFT
        )
        Node.no_workspace_objects.create(
            graph_version=ref_version_draft,
            type=NodeType.SUBGRAPH,
            name="Subgraph to main",
            ref_graph_version=active_graph_version,
            config={},
        )

        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_ids = [g["id"] for g in response.data["result"]["graphs"]]
        # referenced_graph should be excluded because it would create a cycle
        assert str(referenced_graph.id) not in graph_ids

    def test_includes_graph_name_and_description(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
    ):
        """Test that response includes graph name and description."""
        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graphs = response.data["result"]["graphs"]
        assert graphs[0]["name"] == referenced_graph.name
        assert graphs[0]["description"] == referenced_graph.description

    def test_includes_exposed_ports(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
        node_template,
    ):
        """Test that response includes exposed (unconnected) ports of active version."""
        # Create two nodes in the referenced graph's active version
        node_a = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type="atomic",
            name="Node A",
            config={},
            position={"x": 0, "y": 0},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type="atomic",
            name="Node B",
            config={},
            position={"x": 200, "y": 0},
        )

        # Node A: input (exposed) + output (connected to Node B)
        exposed_input = Port.no_workspace_objects.create(
            node=node_a,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
            required=True,
        )
        connected_output = Port.no_workspace_objects.create(
            node=node_a,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Node B: input (connected from Node A) + output (exposed)
        connected_input = Port.no_workspace_objects.create(
            node=node_b,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        exposed_output = Port.no_workspace_objects.create(
            node=node_b,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "number"},
            required=False,
        )

        # Edge: Node A output -> Node B input (these ports become "connected")
        NodeConnection.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            source_node=node_a,
            target_node=node_b,
        )
        Edge.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            source_port=connected_output,
            target_port=connected_input,
        )

        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graphs = response.data["result"]["graphs"]
        assert len(graphs) == 1

        # Get exposed_ports from the version
        assert "versions" in graphs[0]
        assert len(graphs[0]["versions"]) == 1
        exposed_ports = graphs[0]["versions"][0]["exposed_ports"]
        exposed_names = {(p["display_name"], p["direction"]) for p in exposed_ports}

        # Only unconnected ports should appear
        assert ("input1", "input") in exposed_names
        assert ("output1", "output") in exposed_names
        assert len(exposed_ports) == 2

        # Verify port details are included
        input_port_data = next(p for p in exposed_ports if p["direction"] == "input")
        assert input_port_data["id"] == str(exposed_input.id)
        assert input_port_data["display_name"] == "input1"
        assert input_port_data["data_schema"] == {"type": "string"}
        assert input_port_data["required"] is True

        output_port_data = next(p for p in exposed_ports if p["direction"] == "output")
        assert output_port_data["id"] == str(exposed_output.id)

    def test_exposed_ports_empty_when_no_nodes(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
    ):
        """Test that exposed_ports is empty when the active version has no nodes."""
        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graphs = response.data["result"]["graphs"]
        assert len(graphs) == 1
        # Check exposed_ports in the version
        assert "versions" in graphs[0]
        assert len(graphs[0]["versions"]) == 1
        assert graphs[0]["versions"][0]["exposed_ports"] == []

    def test_all_ports_exposed_when_no_edges(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        active_referenced_graph_version,
        node_template,
    ):
        """Test that all ports are exposed when there are no edges."""
        node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type="atomic",
            name="Solo Node",
            config={},
            position={"x": 0, "y": 0},
        )
        Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        url = reverse("graph-referenceable-graphs", kwargs={"pk": str(graph.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # Get exposed_ports from the version
        graphs = response.data["result"]["graphs"]
        assert "versions" in graphs[0]
        assert len(graphs[0]["versions"]) == 1
        exposed_ports = graphs[0]["versions"][0]["exposed_ports"]
        assert len(exposed_ports) == 2
        names = {p["display_name"] for p in exposed_ports}
        assert names == {"input1", "output1"}


# ==================== Version Delete Tests ====================


class TestGraphDeleteVersion:
    """Tests for DELETE /agent-playground/graphs/{id}/versions/{version_id}/"""

    def test_delete_version_success(
        self, authenticated_client, graph, graph_version, active_graph_version
    ):
        """Test successful deletion of a version when multiple versions exist."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["message"] == "Version deleted successfully"

        # Verify soft-deleted
        graph_version.refresh_from_db()
        assert graph_version.deleted is True

    def test_cannot_delete_only_version(
        self, authenticated_client, graph, graph_version
    ):
        """Test that deleting the only version returns 400."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_can_delete_active_version(
        self, authenticated_client, graph, graph_version, active_graph_version
    ):
        """Test that deleting the active version is allowed (leaves graph with no active)."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(active_graph_version.id)},
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK

        active_graph_version.refresh_from_db()
        assert active_graph_version.deleted is True

    def test_delete_cascades_to_nodes(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        node,
        input_port,
    ):
        """Test that deleting a version cascade soft-deletes its nodes and ports."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK

        node.refresh_from_db()
        input_port.refresh_from_db()
        assert node.deleted is True
        assert input_port.deleted is True

    def test_delete_cascades_to_edges(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        output_port,
        second_node_input_port,
        edge,
    ):
        """Test that deleting a version cascade soft-deletes its edges."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK

        edge.refresh_from_db()
        assert edge.deleted is True

    def test_delete_nonexistent_version_returns_404(self, authenticated_client, graph):
        """Test that deleting a nonexistent version returns 404."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(uuid.uuid4())},
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_version_wrong_graph_returns_404(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        referenced_graph_version,
    ):
        """Test that deleting a version belonging to a different graph returns 404."""
        url = reverse(
            "graph-version-detail",
            kwargs={
                "pk": str(graph.id),
                "version_id": str(referenced_graph_version.id),
            },
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_requires_authentication(
        self, api_client, graph, graph_version, active_graph_version
    ):
        """Test that version delete requires authentication."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.delete(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


# ==================== Activate Version Tests ====================


class TestGraphActivateVersion:
    """Tests for POST /agent-playground/graphs/{id}/versions/{version_id}/activate/"""

    def test_activate_inactive_version_success(
        self, authenticated_client, graph, graph_version
    ):
        """Test that an inactive version can be activated."""
        inactive_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.INACTIVE,
        )

        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(inactive_version.id)},
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["status"] == "active"
        assert response.data["result"]["id"] == str(inactive_version.id)

        inactive_version.refresh_from_db()
        assert inactive_version.status == GraphVersionStatus.ACTIVE

    def test_activate_demotes_current_active_version(
        self, authenticated_client, graph, active_graph_version
    ):
        """Test that activating a version demotes the currently active version to inactive."""
        inactive_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=3,
            status=GraphVersionStatus.INACTIVE,
        )

        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(inactive_version.id)},
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK

        # New version is now active
        inactive_version.refresh_from_db()
        assert inactive_version.status == GraphVersionStatus.ACTIVE

        # Old active version is now inactive
        active_graph_version.refresh_from_db()
        assert active_graph_version.status == GraphVersionStatus.INACTIVE

    def test_cannot_activate_draft_version(
        self, authenticated_client, graph, graph_version
    ):
        """Test that a draft version cannot be activated."""
        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_activate_already_active_version(
        self, authenticated_client, graph, active_graph_version
    ):
        """Test that an already active version cannot be activated again."""
        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(active_graph_version.id)},
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_activate_nonexistent_version_returns_404(
        self, authenticated_client, graph
    ):
        """Test that activating a nonexistent version returns 404."""
        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(uuid.uuid4())},
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_activate_version_wrong_graph_returns_404(
        self,
        authenticated_client,
        graph,
        referenced_graph,
        referenced_graph_version,
    ):
        """Test that activating a version belonging to a different graph returns 404."""
        url = reverse(
            "graph-version-activate",
            kwargs={
                "pk": str(graph.id),
                "version_id": str(referenced_graph_version.id),
            },
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_activate_requires_authentication(self, api_client, graph, graph_version):
        """Test that activate requires authentication."""
        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.post(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_activate_returns_version_detail_with_nodes(
        self, authenticated_client, graph, dynamic_node_template
    ):
        """Test that activate returns full version detail including nodes and edges."""
        inactive_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.INACTIVE,
        )
        Node.no_workspace_objects.create(
            graph_version=inactive_version,
            node_template=dynamic_node_template,
            type=NodeType.ATOMIC,
            name="Test Node",
            config={},
            position={"x": 100, "y": 100},
        )

        url = reverse(
            "graph-version-activate",
            kwargs={"pk": str(graph.id), "version_id": str(inactive_version.id)},
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert "nodes" in response.data["result"]
        assert len(response.data["result"]["nodes"]) == 1


# ==================== INACTIVE Version Update Tests ====================


class TestGraphUpdateInactiveVersion:
    """Tests for updating INACTIVE versions."""

    def test_cannot_update_inactive_version(self, authenticated_client, graph):
        """Test that INACTIVE versions cannot be updated."""
        inactive_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.INACTIVE,
        )

        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(inactive_version.id)},
        )
        data = {"commit_message": "try update"}
        response = authenticated_client.patch(url, data=data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==================== Auth Tests for Version Endpoints ====================


class TestVersionEndpointAuthentication:
    """Tests that all version endpoints require authentication."""

    def test_list_versions_requires_auth(self, api_client, graph):
        """Test that list_versions requires authentication."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = api_client.get(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_version_requires_auth(self, api_client, graph):
        """Test that create_version requires authentication."""
        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = api_client.post(url, format="json")

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_retrieve_version_requires_auth(self, api_client, graph, graph_version):
        """Test that retrieve_version requires authentication."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.get(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_update_version_requires_auth(self, api_client, graph, graph_version):
        """Test that update_version requires authentication."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.patch(url, data={"commit_message": "x"}, format="json")

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_delete_version_requires_auth(self, api_client, graph, graph_version):
        """Test that delete_version requires authentication."""
        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.delete(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES


# ==================== Cross-Org Isolation for Version Endpoints ====================


class TestVersionCrossOrgIsolation:
    """Tests that version endpoints enforce org-level isolation."""

    def test_cannot_list_versions_of_other_org_graph(
        self, api_client, graph, graph_version, workspace, db
    ):
        """Test that users cannot list versions of another org's graph."""
        other_org = Organization.objects.create(name="Other Org")
        other_user = User.objects.create_user(
            email="crossorg@test.com",
            password="testpass",
            name="Cross Org User",
            organization=other_org,
        )
        api_client.force_authenticate(user=other_user)
        api_client.set_organization(other_org)

        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = api_client.get(url)

        api_client.stop_workspace_injection()

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_create_version_on_other_org_graph(
        self, api_client, graph, workspace, db
    ):
        """Test that users cannot create versions on another org's graph."""
        other_org = Organization.objects.create(name="Other Org 2")
        other_user = User.objects.create_user(
            email="crossorg2@test.com",
            password="testpass",
            name="Cross Org User 2",
            organization=other_org,
        )
        api_client.force_authenticate(user=other_user)
        api_client.set_organization(other_org)

        url = reverse("graph-versions", kwargs={"pk": str(graph.id)})
        response = api_client.post(url, format="json")

        api_client.stop_workspace_injection()

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_update_version_on_other_org_graph(
        self, api_client, graph, graph_version, workspace, db
    ):
        """Test that users cannot update versions on another org's graph."""
        other_org = Organization.objects.create(name="Other Org 3")
        other_user = User.objects.create_user(
            email="crossorg3@test.com",
            password="testpass",
            name="Cross Org User 3",
            organization=other_org,
        )
        api_client.force_authenticate(user=other_user)
        api_client.set_organization(other_org)

        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.patch(url, data={"commit_message": "x"}, format="json")

        api_client.stop_workspace_injection()

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cannot_delete_version_on_other_org_graph(
        self, api_client, graph, graph_version, active_graph_version, workspace, db
    ):
        """Test that users cannot delete versions on another org's graph."""
        other_org = Organization.objects.create(name="Other Org 4")
        other_user = User.objects.create_user(
            email="crossorg4@test.com",
            password="testpass",
            name="Cross Org User 4",
            organization=other_org,
        )
        api_client.force_authenticate(user=other_user)
        api_client.set_organization(other_org)

        url = reverse(
            "graph-version-detail",
            kwargs={"pk": str(graph.id), "version_id": str(graph_version.id)},
        )
        response = api_client.delete(url)

        api_client.stop_workspace_injection()

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==================== Node Count Tests ====================


class TestGraphNodeCount:
    """Tests for node_count field in graph list response."""

    def test_node_count_reflects_active_version_nodes(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_in_active_version,
        node_template,
    ):
        """Test that node_count returns the node count from the active version."""
        # Add a second node to the active version
        Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type="atomic",
            name="Second Active Node",
            config={},
            position={"x": 200, "y": 200},
        )

        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_data = next(
            g for g in response.data["result"]["graphs"] if g["id"] == str(graph.id)
        )
        assert graph_data["node_count"] == 2

    def test_node_count_zero_when_no_active_version(
        self, authenticated_client, graph, graph_version
    ):
        """Test that node_count is 0 when no active version exists."""
        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_data = response.data["result"]["graphs"][0]
        assert graph_data["node_count"] == 0

    def test_node_count_zero_for_empty_active_version(
        self, authenticated_client, graph, active_graph_version
    ):
        """Test that node_count is 0 for an active version with no nodes."""
        url = reverse("graph-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        graph_data = next(
            g for g in response.data["result"]["graphs"] if g["id"] == str(graph.id)
        )
        assert graph_data["node_count"] == 0

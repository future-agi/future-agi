"""
Tests for the GraphVersion model.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from agent_playground.models import Edge, Graph, GraphVersion, Node, Port
from agent_playground.models.choices import GraphVersionStatus, PortMode
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_template import NodeTemplate


@pytest.mark.unit
class TestGraphVersionCreation:
    """Tests for GraphVersion model creation."""

    def test_graph_version_creation_success(self, db, graph):
        """Basic creation."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
            tags=["test"],
        )
        assert version.id is not None
        assert version.version_number == 1
        assert version.status == GraphVersionStatus.DRAFT
        assert version.graph == graph

    def test_graph_version_draft_status(self, db, graph):
        """status=DRAFT is valid."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
            tags=["test"],
        )
        version.full_clean()
        assert version.status == GraphVersionStatus.DRAFT

    def test_graph_version_active_status(self, db, graph):
        """status=ACTIVE is valid."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
            tags=["production"],
        )
        version.full_clean()
        assert version.status == GraphVersionStatus.ACTIVE

    def test_graph_version_inactive_status(self, db, graph):
        """status=INACTIVE for historical versions."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.INACTIVE,
            tags=["archived"],
        )
        version.full_clean()
        assert version.status == GraphVersionStatus.INACTIVE


@pytest.mark.unit
class TestGraphVersionUniqueConstraint:
    """Tests for GraphVersion unique constraints."""

    def test_graph_version_unique_version_number(self, db, graph):
        """UniqueConstraint on (graph, version_number)."""
        GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )

        with pytest.raises(IntegrityError):
            GraphVersion.no_workspace_objects.create(
                graph=graph,
                version_number=1,
                status=GraphVersionStatus.DRAFT,
            )

    def test_graph_version_unique_allows_deleted(self, db, graph):
        """Soft-deleted versions don't block uniqueness."""
        version1 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        version1.delete()  # Soft delete

        # Should be able to create another version with same number
        version2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        assert version2.id is not None
        assert version2.version_number == 1


@pytest.mark.unit
class TestGraphVersionActiveConstraint:
    """Tests for only one active version per graph."""

    def test_graph_version_only_one_active(self, db, graph):
        """clean() prevents multiple active versions."""
        GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )

        version2 = GraphVersion(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.ACTIVE,
        )
        with pytest.raises(
            ValidationError, match="Only one active version allowed per graph"
        ):
            version2.full_clean()

    def test_graph_version_multiple_drafts_allowed(self, db, graph):
        """Multiple drafts per graph OK."""
        GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        version2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.DRAFT,
            tags=["test"],
        )
        version2.full_clean()  # Should not raise
        assert version2.status == GraphVersionStatus.DRAFT


@pytest.mark.unit
class TestGraphVersionCascadeDelete:
    """Tests for GraphVersion cascade delete behavior."""

    def test_graph_version_cascade_delete_graph(self, db, graph):
        """Deleting graph cascades to versions."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        version_id = version.id

        # Hard delete the graph
        Graph.all_objects.filter(id=graph.id).delete()

        # Version should be gone
        assert not GraphVersion.all_objects.filter(id=version_id).exists()


@pytest.mark.unit
class TestGraphVersionActivationValidation:
    """Tests for exposed output port display_name uniqueness on activation."""

    @pytest.fixture
    def dynamic_template(self, db):
        """Dynamic template for activation tests needing arbitrary port keys."""
        return NodeTemplate.no_workspace_objects.create(
            name="activation_test_template",
            display_name="Activation Test Template",
            description="Template for activation tests",
            categories=["testing"],
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={},
        )

    def test_activation_succeeds_with_unique_output_display_names(
        self, db, graph, dynamic_template
    ):
        """Two output ports with same key but different display_name — activation succeeds."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=10, status=GraphVersionStatus.DRAFT
        )
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node A",
            config={},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node B",
            config={},
        )
        Port.no_workspace_objects.create(
            node=node_a,
            key="custom",
            display_name="Node A Response",
            direction="output",
        )
        Port.no_workspace_objects.create(
            node=node_b,
            key="custom",
            display_name="Node B Response",
            direction="output",
        )
        version.status = GraphVersionStatus.ACTIVE
        version.save()  # Should not raise

    def test_activation_blocked_with_duplicate_output_display_names(
        self, db, graph, dynamic_template
    ):
        """Two unconnected output ports with same display_name — activation blocked."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=11, status=GraphVersionStatus.DRAFT
        )
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node A",
            config={},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node B",
            config={},
        )
        Port.no_workspace_objects.create(
            node=node_a,
            key="custom",
            display_name="response",
            direction="output",
        )
        Port.no_workspace_objects.create(
            node=node_b,
            key="custom",
            display_name="response",
            direction="output",
        )
        version.status = GraphVersionStatus.ACTIVE
        with pytest.raises(
            ValidationError, match="Duplicate exposed output port display name"
        ):
            version.save()

    def test_activation_allows_duplicate_input_display_names(
        self, db, graph, dynamic_template
    ):
        """Two unconnected input ports with same display_name — activation succeeds (broadcast)."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=12, status=GraphVersionStatus.DRAFT
        )
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node A",
            config={},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node B",
            config={},
        )
        Port.no_workspace_objects.create(
            node=node_a,
            key="custom",
            display_name="prompt",
            direction="input",
        )
        Port.no_workspace_objects.create(
            node=node_b,
            key="custom",
            display_name="prompt",
            direction="input",
        )
        version.status = GraphVersionStatus.ACTIVE
        version.save()  # Should not raise

    def test_activation_allows_connected_outputs_with_same_display_name(
        self, db, graph, dynamic_template
    ):
        """Connected output ports with same display_name are fine (they're not exposed)."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=13, status=GraphVersionStatus.DRAFT
        )
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node A",
            config={},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Node B",
            config={},
        )
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="custom",
            display_name="response",
            direction="output",
        )
        in_b = Port.no_workspace_objects.create(
            node=node_b,
            key="custom",
            display_name="input1",
            direction="input",
        )
        # Also create an unconnected output on node_b with same display_name
        Port.no_workspace_objects.create(
            node=node_b,
            key="custom",
            display_name="response",
            direction="output",
        )
        # Connect node_a output to node_b input — node_a's "response" is now connected
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_b
        )
        Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_b,
        )
        # Only node_b's "response" output is unconnected, so no duplicate
        version.status = GraphVersionStatus.ACTIVE
        version.save()  # Should not raise

    def test_activation_with_no_ports(self, db, graph, dynamic_template):
        """Activation succeeds when version has no ports (dynamic template)."""
        version = GraphVersion.no_workspace_objects.create(
            graph=graph, version_number=14, status=GraphVersionStatus.DRAFT
        )
        Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type="atomic",
            name="Empty Node",
            config={},
        )
        version.status = GraphVersionStatus.ACTIVE
        version.save()  # Should not raise

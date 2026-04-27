"""
Tests for the Edge model.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from agent_playground.models import Edge, GraphVersion, Node, Port
from agent_playground.models.choices import (
    GraphVersionStatus,
    NodeType,
    PortDirection,
)
from agent_playground.models.node_connection import NodeConnection


@pytest.mark.unit
class TestEdgeCreation:
    """Tests for Edge model creation."""

    def test_edge_creation_success(
        self, db, graph_version, node, second_node, output_port, second_node_input_port
    ):
        """Valid edge between output->input."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=second_node,
        )
        edge = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        assert edge.id is not None
        assert edge.source_port == output_port
        assert edge.target_port == second_node_input_port


@pytest.mark.unit
class TestEdgeDirectionValidation:
    """Tests for edge port direction validation."""

    def test_edge_source_must_be_output(
        self, db, graph_version, input_port, second_node_input_port
    ):
        """source_port.direction must be OUTPUT."""
        edge = Edge(
            graph_version=graph_version,
            source_port=input_port,  # Wrong - should be output
            target_port=second_node_input_port,
        )
        with pytest.raises(ValidationError, match="Source port must be an output port"):
            edge.clean()

    def test_edge_target_must_be_input(
        self, db, graph_version, output_port, second_node_output_port
    ):
        """target_port.direction must be INPUT."""
        edge = Edge(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_output_port,  # Wrong - should be input
        )
        with pytest.raises(ValidationError, match="Target port must be an input port"):
            edge.clean()


@pytest.mark.unit
class TestEdgeGraphVersionValidation:
    """Tests for edge graph version validation."""

    def test_edge_source_must_belong_to_graph_version(
        self, db, graph_version, second_node_input_port, graph, node_template
    ):
        """source_port.node.graph_version must match edge's graph_version."""
        # Create another graph version
        other_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=99,
            status=GraphVersionStatus.INACTIVE,
        )
        other_node = Node.no_workspace_objects.create(
            graph_version=other_version,
            node_template=node_template,
            type="atomic",
            name="Other Node",
            config={},
        )
        other_output = Port.no_workspace_objects.create(
            node=other_node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )

        edge = Edge(
            graph_version=graph_version,
            source_port=other_output,  # Different graph version
            target_port=second_node_input_port,
        )
        with pytest.raises(
            ValidationError, match="Source port node must belong to this graph version"
        ):
            edge.clean()

    def test_edge_target_must_belong_to_graph_version(
        self, db, graph_version, output_port, graph, node_template
    ):
        """target_port.node.graph_version must match edge's graph_version."""
        # Create another graph version
        other_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=99,
            status=GraphVersionStatus.INACTIVE,
        )
        other_node = Node.no_workspace_objects.create(
            graph_version=other_version,
            node_template=node_template,
            type="atomic",
            name="Other Node",
            config={},
        )
        other_input = Port.no_workspace_objects.create(
            node=other_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        edge = Edge(
            graph_version=graph_version,
            source_port=output_port,
            target_port=other_input,  # Different graph version
        )
        with pytest.raises(
            ValidationError, match="Target port node must belong to this graph version"
        ):
            edge.clean()


@pytest.mark.unit
class TestEdgeUniqueConstraint:
    """Tests for Edge unique constraints."""

    def test_edge_unique_connection(
        self, db, graph_version, node, second_node, output_port, second_node_input_port
    ):
        """UniqueConstraint on (source, target)."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=second_node,
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )

        with pytest.raises(IntegrityError):
            Edge.no_workspace_objects.create(
                graph_version=graph_version,
                source_port=output_port,
                target_port=second_node_input_port,  # Duplicate connection
            )

    def test_edge_fan_in_blocked(
        self, db, graph_version, second_node_input_port, node, second_node, third_node
    ):
        """One input port cannot have multiple sources (fan-in blocked)."""
        # Create two output ports from different nodes (not the target node)
        output1 = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )
        output2 = Port.no_workspace_objects.create(
            node=third_node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )

        # Create NodeConnections
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=second_node,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=third_node,
            target_node=second_node,
        )

        # Create first edge to input
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output1,
            target_port=second_node_input_port,
        )

        # Try to create second edge to same input - should fail
        with pytest.raises(IntegrityError):
            Edge.no_workspace_objects.create(
                graph_version=graph_version,
                source_port=output2,
                target_port=second_node_input_port,  # Same target - fan-in blocked
            )

    def test_edge_fan_out_allowed(
        self, db, graph_version, output_port, node, second_node, node_template
    ):
        """One output port can connect to multiple inputs (fan-out allowed)."""
        # Create first input
        input1 = Port.no_workspace_objects.create(
            node=second_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        # Create third node with another input
        third_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type="atomic",
            name="Third Node",
            config={},
        )
        input2 = Port.no_workspace_objects.create(
            node=third_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        # Create NodeConnections
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=second_node,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=third_node,
        )

        # Create edges from same output to multiple inputs
        edge1 = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=input1,
        )
        edge2 = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,  # Same source
            target_port=input2,  # Different target
        )
        assert edge1.source_port == edge2.source_port

    def test_edge_unique_allows_deleted(
        self, db, graph_version, node, second_node, output_port, second_node_input_port
    ):
        """Soft-deleted edges don't block uniqueness."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=second_node,
        )
        edge1 = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        edge1.delete()  # Soft delete

        # Should be able to create same connection
        edge2 = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        assert edge2.id is not None


@pytest.mark.unit
class TestEdgeCascadeDelete:
    """Tests for Edge cascade delete behavior."""

    def test_edge_cascade_delete_graph_version(self, db, edge, graph_version):
        """Deleting version cascades to edges."""
        edge_id = edge.id

        # Hard delete the graph version
        GraphVersion.all_objects.filter(id=graph_version.id).delete()

        # Edge should be gone
        assert not Edge.all_objects.filter(id=edge_id).exists()

    def test_edge_cascade_delete_source_port(self, db, edge, output_port):
        """Deleting source port cascades to edge."""
        edge_id = edge.id

        # Hard delete the source port
        Port.all_objects.filter(id=output_port.id).delete()

        # Edge should be gone
        assert not Edge.all_objects.filter(id=edge_id).exists()

    def test_edge_cascade_delete_target_port(self, db, edge, second_node_input_port):
        """Deleting target port cascades to edge."""
        edge_id = edge.id

        # Hard delete the target port
        Port.all_objects.filter(id=second_node_input_port.id).delete()

        # Edge should be gone
        assert not Edge.all_objects.filter(id=edge_id).exists()


@pytest.mark.unit
class TestEdgeCycleValidation:
    """Tests for cycle detection during edge validation."""

    def test_self_loop_rejected(self, db, graph_version, node, output_port, input_port):
        """An edge from a node back to itself is rejected (NC validation fires first)."""
        edge = Edge(
            graph_version=graph_version,
            source_port=output_port,
            target_port=input_port,
        )
        with pytest.raises(ValidationError, match="NodeConnection"):
            edge.clean()

    def test_two_node_cycle_rejected(
        self,
        db,
        graph_version,
        node,
        second_node,
        output_port,
        second_node_input_port,
        second_node_output_port,
        input_port,
    ):
        """A→B exists, B→A is rejected."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=second_node,
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=second_node,
            target_node=node,
        )
        reverse_edge = Edge(
            graph_version=graph_version,
            source_port=second_node_output_port,
            target_port=input_port,
        )
        with pytest.raises(
            ValidationError, match="This edge would create a cycle in the graph"
        ):
            reverse_edge.clean()

    def test_three_node_cycle_rejected(
        self,
        db,
        graph_version,
        node,
        second_node,
        third_node,
        output_port,
        second_node_input_port,
        second_node_output_port,
        third_node_input_port,
        third_node_output_port,
        input_port,
    ):
        """A→B→C exists, C→A is rejected."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=second_node
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=second_node, target_node=third_node
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=second_node_output_port,
            target_port=third_node_input_port,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=third_node, target_node=node
        )
        closing_edge = Edge(
            graph_version=graph_version,
            source_port=third_node_output_port,
            target_port=input_port,
        )
        with pytest.raises(
            ValidationError, match="This edge would create a cycle in the graph"
        ):
            closing_edge.clean()

    def test_valid_chain_accepted(
        self,
        db,
        graph_version,
        node,
        second_node,
        third_node,
        output_port,
        second_node_input_port,
        second_node_output_port,
        third_node_input_port,
    ):
        """A→B exists, B→C is valid (no cycle)."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=second_node
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=second_node, target_node=third_node
        )
        chain_edge = Edge(
            graph_version=graph_version,
            source_port=second_node_output_port,
            target_port=third_node_input_port,
        )
        chain_edge.clean()  # Should not raise

    def test_fan_out_accepted(
        self,
        db,
        graph_version,
        node,
        second_node,
        third_node,
        output_port,
        second_node_input_port,
        third_node_input_port,
    ):
        """A→B exists, A→C is valid (fan-out, no cycle)."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=second_node
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=third_node
        )
        fan_out_edge = Edge(
            graph_version=graph_version,
            source_port=output_port,
            target_port=third_node_input_port,
        )
        fan_out_edge.clean()  # Should not raise

    def test_diamond_dag_accepted(
        self,
        db,
        graph_version,
        node,
        output_port,
        second_node,
        second_node_input_port,
        second_node_output_port,
        third_node,
        third_node_input_port,
        third_node_output_port,
        dynamic_node_template,
    ):
        """Diamond DAG: A→B, A→C, B→D exists, C→D is valid."""
        # Create fourth node (D) with dynamic template to allow multiple input ports
        fourth_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=dynamic_node_template,
            type=NodeType.ATOMIC,
            name="Fourth Node",
            config={},
            position={"x": 700, "y": 100},
        )
        fourth_input_1 = Port.no_workspace_objects.create(
            node=fourth_node,
            key="custom",
            display_name="Input From B",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        fourth_input_2 = Port.no_workspace_objects.create(
            node=fourth_node,
            key="custom",
            display_name="Input From C",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )

        # Create NodeConnections
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=second_node
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=third_node
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=second_node,
            target_node=fourth_node,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=third_node, target_node=fourth_node
        )

        # A→B
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        # A→C
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=third_node_input_port,
        )
        # B→D
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=second_node_output_port,
            target_port=fourth_input_1,
        )
        # C→D should be valid (diamond, not cycle)
        diamond_edge = Edge(
            graph_version=graph_version,
            source_port=third_node_output_port,
            target_port=fourth_input_2,
        )
        diamond_edge.clean()  # Should not raise

    def test_soft_deleted_edge_ignored(
        self,
        db,
        graph_version,
        node,
        second_node,
        output_port,
        second_node_input_port,
        second_node_output_port,
        input_port,
    ):
        """Soft-deleted A→B should not block B→A."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=second_node
        )
        edge_ab = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )
        edge_ab.delete()  # Soft delete

        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=second_node, target_node=node
        )
        reverse_edge = Edge(
            graph_version=graph_version,
            source_port=second_node_output_port,
            target_port=input_port,
        )
        reverse_edge.clean()  # Should not raise

    def test_other_graph_version_ignored(
        self,
        db,
        graph,
        graph_version,
        node,
        second_node,
        output_port,
        second_node_input_port,
        node_template,
    ):
        """A→B in version 1, B→A in version 2 is valid (different versions)."""
        # A→B in graph_version
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node, target_node=second_node
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=second_node_input_port,
        )

        # Create version 2 with its own nodes and ports
        version2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=10,
            status=GraphVersionStatus.DRAFT,
        )
        node_b2 = Node.no_workspace_objects.create(
            graph_version=version2,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node B v2",
            config={},
        )
        node_a2 = Node.no_workspace_objects.create(
            graph_version=version2,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node A v2",
            config={},
        )
        b2_output = Port.no_workspace_objects.create(
            node=node_b2,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )
        a2_input = Port.no_workspace_objects.create(
            node=node_a2,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        # B→A in version 2 should be valid
        NodeConnection.no_workspace_objects.create(
            graph_version=version2, source_node=node_b2, target_node=node_a2
        )
        cross_version_edge = Edge(
            graph_version=version2,
            source_port=b2_output,
            target_port=a2_input,
        )
        cross_version_edge.clean()  # Should not raise


@pytest.mark.unit
class TestEdgeCycleValidationNullWorkspace:
    """Tests for cycle detection when graph has workspace=None."""

    def test_self_loop_rejected_null_workspace(
        self,
        db,
        graph_version_no_ws,
        node_a_no_ws,
        node_a_no_ws_output,
        node_a_no_ws_input,
    ):
        """Self-loop on a node in a graph with no workspace is rejected (NC validation fires first)."""
        edge = Edge(
            graph_version=graph_version_no_ws,
            source_port=node_a_no_ws_output,
            target_port=node_a_no_ws_input,
        )
        with pytest.raises(ValidationError, match="NodeConnection"):
            edge.clean()

    def test_two_node_cycle_rejected_null_workspace(
        self,
        db,
        graph_version_no_ws,
        node_a_no_ws,
        node_b_no_ws,
        node_a_no_ws_output,
        node_a_no_ws_input,
        node_b_no_ws_input,
        node_b_no_ws_output,
    ):
        """A->B exists, B->A is rejected (null workspace)."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            source_node=node_a_no_ws,
            target_node=node_b_no_ws,
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            source_port=node_a_no_ws_output,
            target_port=node_b_no_ws_input,
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            source_node=node_b_no_ws,
            target_node=node_a_no_ws,
        )
        reverse_edge = Edge(
            graph_version=graph_version_no_ws,
            source_port=node_b_no_ws_output,
            target_port=node_a_no_ws_input,
        )
        with pytest.raises(
            ValidationError, match="This edge would create a cycle in the graph"
        ):
            reverse_edge.clean()

    def test_valid_chain_accepted_null_workspace(
        self,
        db,
        graph_version_no_ws,
        node_a_no_ws,
        node_b_no_ws,
        node_a_no_ws_output,
        node_b_no_ws_input,
        node_b_no_ws_output,
        node_template,
    ):
        """A->B exists, B->C is valid (null workspace, no cycle)."""
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            source_node=node_a_no_ws,
            target_node=node_b_no_ws,
        )
        Edge.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            source_port=node_a_no_ws_output,
            target_port=node_b_no_ws_input,
        )
        # Create node C
        node_c = Node.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node C (no ws)",
            config={},
            position={"x": 500, "y": 100},
        )
        node_c_input = Port.no_workspace_objects.create(
            node=node_c,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version_no_ws,
            source_node=node_b_no_ws,
            target_node=node_c,
        )
        chain_edge = Edge(
            graph_version=graph_version_no_ws,
            source_port=node_b_no_ws_output,
            target_port=node_c_input,
        )
        chain_edge.clean()  # Should not raise

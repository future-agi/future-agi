import pytest
from django.core.exceptions import ValidationError

from agent_playground.models import (
    Edge,
    ExecutionData,
    Graph,
    GraphExecution,
    GraphVersion,
    Node,
    NodeExecution,
    NodeTemplate,
    Port,
)
from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
    NodeExecutionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.node_connection import NodeConnection


@pytest.mark.integration
class TestGraphConstructionWorkflow:
    """Test building a complete graph from scratch as a cohesive workflow."""

    def test_build_complete_graph(self, db, organization, workspace, user):
        """Build Graph → Version → 2 Nodes → Ports → Edge and verify all relationships."""
        # 1. Create graph
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Complete Graph",
            created_by=user,
        )

        # 2. Create version
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )

        # 3. Create node template
        template = NodeTemplate.no_workspace_objects.create(
            name="integration_template",
            display_name="Integration Template",
            description="Template for integration tests",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={"type": "object", "properties": {}},
        )

        # 4. Create two nodes
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Node A",
            config={},
            position={"x": 0, "y": 0},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Node B",
            config={},
            position={"x": 200, "y": 0},
        )

        # 5. Create ports
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        in_b = Port.no_workspace_objects.create(
            node=node_b,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )

        # 6. Create edge
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_b
        )
        edge = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_b,
        )

        # Verify all relationships
        assert version.graph == graph
        assert node_a.graph_version == version
        assert node_b.graph_version == version
        assert out_a.node == node_a
        assert in_b.node == node_b
        assert edge.source_port == out_a
        assert edge.target_port == in_b
        assert edge.graph_version == version

        # Verify reverse relationships
        assert version in graph.versions.all()
        assert node_a in version.nodes.all()
        assert node_b in version.nodes.all()
        assert out_a in node_a.ports.all()
        assert in_b in node_b.ports.all()
        assert edge in version.edges.all()

        # Verify property traversals via graph
        assert version.graph.organization == organization
        assert version.graph.workspace == workspace

    def test_add_third_node_extends_graph(self, db, organization, workspace, user):
        """Extend an existing 2-node graph with a third node + edges, verify DAG."""
        # Build initial 2-node graph: A → B
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Extendable Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="extend_template",
            display_name="Extend Template",
            description="For extension test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="A",
            config={},
            position={"x": 0, "y": 0},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="B",
            config={},
            position={"x": 200, "y": 0},
        )
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        in_b = Port.no_workspace_objects.create(
            node=node_b,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_b = Port.no_workspace_objects.create(
            node=node_b,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_b
        )
        Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_b,
        )

        # Add Node C: B → C  (extends the DAG)
        node_c = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="C",
            config={},
            position={"x": 400, "y": 0},
        )
        in_c = Port.no_workspace_objects.create(
            node=node_c,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_b, target_node=node_c
        )
        edge_bc = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_b,
            target_port=in_c,
        )

        # Verify extended graph
        assert version.nodes.count() == 3
        assert version.edges.count() == 2
        assert edge_bc.source_port.node == node_b
        assert edge_bc.target_port.node == node_c

        # Verify DAG: edge validation passes (no cycle in A → B → C)
        edge_bc.full_clean()

    def test_build_graph_with_ports_on_start_and_end_nodes(
        self, db, organization, workspace, user
    ):
        """Build A → B → C chain with input port on start node and output port on end node."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Start-End Ports Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="start_end_template",
            display_name="Start-End Template",
            description="For start/end port test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )

        # Create three nodes
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="A",
            config={},
            position={"x": 0, "y": 0},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="B",
            config={},
            position={"x": 200, "y": 0},
        )
        node_c = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="C",
            config={},
            position={"x": 400, "y": 0},
        )

        # Start node A: input port (graph entry) + output port
        in_a = Port.no_workspace_objects.create(
            node=node_a,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Middle node B: input + output ports
        in_b = Port.no_workspace_objects.create(
            node=node_b,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_b = Port.no_workspace_objects.create(
            node=node_b,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # End node C: input port + output port (graph exit)
        in_c = Port.no_workspace_objects.create(
            node=node_c,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_c = Port.no_workspace_objects.create(
            node=node_c,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Create edges: A.out → B.in, B.out → C.in
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_b
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_b, target_node=node_c
        )
        edge_ab = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_b,
        )
        edge_bc = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_b,
            target_port=in_c,
        )

        # All ports exist on their respective nodes
        assert in_a in node_a.ports.all()
        assert out_a in node_a.ports.all()
        assert in_b in node_b.ports.all()
        assert out_b in node_b.ports.all()
        assert in_c in node_c.ports.all()
        assert out_c in node_c.ports.all()

        # Start node's input port has no incoming edges
        assert not Edge.no_workspace_objects.filter(target_port=in_a).exists()

        # End node's output port has no outgoing edges
        assert not Edge.no_workspace_objects.filter(source_port=out_c).exists()

        # Edge validation passes (valid DAG)
        edge_ab.full_clean()
        edge_bc.full_clean()

        # Port counts per node
        assert node_a.ports.count() == 2
        assert node_b.ports.count() == 2
        assert node_c.ports.count() == 2

    def test_build_diamond_dag(self, db, organization, workspace, user):
        """Build diamond DAG: A → B, A → C, B → D, C → D with fan-out and fan-in."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Diamond DAG",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="diamond_template",
            display_name="Diamond Template",
            description="For diamond DAG test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )

        # Node A: 1 output port (fan-out)
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="A",
            config={},
            position={"x": 200, "y": 0},
        )
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Node B: 1 input, 1 output
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="B",
            config={},
            position={"x": 0, "y": 200},
        )
        in_b = Port.no_workspace_objects.create(
            node=node_b,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_b = Port.no_workspace_objects.create(
            node=node_b,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Node C: 1 input, 1 output
        node_c = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="C",
            config={},
            position={"x": 400, "y": 200},
        )
        in_c = Port.no_workspace_objects.create(
            node=node_c,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_c = Port.no_workspace_objects.create(
            node=node_c,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Node D: 2 input ports (separate for fan-in), no fan-in violation
        # Use a dynamic template since D needs multiple input ports
        dynamic_template = NodeTemplate.no_workspace_objects.create(
            name="diamond_dynamic_template",
            display_name="Diamond Dynamic Template",
            description="For diamond DAG node D (multiple inputs)",
            categories=["test"],
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={},
        )
        node_d = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=dynamic_template,
            type=NodeType.ATOMIC,
            name="D",
            config={},
            position={"x": 200, "y": 400},
        )
        in_d_from_b = Port.no_workspace_objects.create(
            node=node_d,
            key="custom",
            display_name="in_from_b",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        in_d_from_c = Port.no_workspace_objects.create(
            node=node_d,
            key="custom",
            display_name="in_from_c",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )

        # Edges: A.out → B.in, A.out → C.in (fan-out), B.out → D.in_from_b, C.out → D.in_from_c
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_b
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_c
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_b, target_node=node_d
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_c, target_node=node_d
        )
        edge_ab = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_b,
        )
        edge_ac = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_c,
        )
        edge_bd = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_b,
            target_port=in_d_from_b,
        )
        edge_cd = Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_c,
            target_port=in_d_from_c,
        )

        # 4 nodes, 4 edges
        assert version.nodes.count() == 4
        assert version.edges.count() == 4

        # Fan-out: A.out has 2 outgoing edges
        assert Edge.no_workspace_objects.filter(source_port=out_a).count() == 2

        # No fan-in violation: each D input port has exactly 1 incoming edge
        assert Edge.no_workspace_objects.filter(target_port=in_d_from_b).count() == 1
        assert Edge.no_workspace_objects.filter(target_port=in_d_from_c).count() == 1

        # All edges pass full_clean() (no cycles)
        edge_ab.full_clean()
        edge_ac.full_clean()
        edge_bd.full_clean()
        edge_cd.full_clean()

    def test_cycle_detection_rejects_back_edge(self, db, organization, workspace, user):
        """Adding C → A to chain A → B → C should raise ValidationError for cycle."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Cycle Back Edge Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="cycle_back_template",
            display_name="Cycle Back Template",
            description="For back-edge cycle test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )

        # Create nodes A, B, C with ports
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="A",
            config={},
            position={"x": 0, "y": 0},
        )
        in_a = Port.no_workspace_objects.create(
            node=node_a,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="B",
            config={},
            position={"x": 200, "y": 0},
        )
        in_b = Port.no_workspace_objects.create(
            node=node_b,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_b = Port.no_workspace_objects.create(
            node=node_b,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        node_c = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="C",
            config={},
            position={"x": 400, "y": 0},
        )
        in_c = Port.no_workspace_objects.create(
            node=node_c,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_c = Port.no_workspace_objects.create(
            node=node_c,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Create valid chain: A → B → C
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_a, target_node=node_b
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_b, target_node=node_c
        )
        NodeConnection.no_workspace_objects.create(
            graph_version=version, source_node=node_c, target_node=node_a
        )
        Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_a,
            target_port=in_b,
        )
        Edge.no_workspace_objects.create(
            graph_version=version,
            source_port=out_b,
            target_port=in_c,
        )

        # Attempt back edge C → A — should create a cycle
        back_edge = Edge(
            graph_version=version,
            source_port=out_c,
            target_port=in_a,
        )
        with pytest.raises(ValidationError, match="cycle"):
            back_edge.full_clean()

    def test_cycle_detection_rejects_self_loop(self, db, organization, workspace, user):
        """Connecting a node's output to its own input should raise ValidationError."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Self Loop Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="self_loop_template",
            display_name="Self Loop Template",
            description="For self-loop cycle test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )

        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="A",
            config={},
            position={"x": 0, "y": 0},
        )
        in_a = Port.no_workspace_objects.create(
            node=node_a,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        out_a = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Attempt self-loop: A.out → A.in
        self_loop = Edge(
            graph_version=version,
            source_port=out_a,
            target_port=in_a,
        )
        with pytest.raises(ValidationError, match="NodeConnection"):
            self_loop.full_clean()


@pytest.mark.integration
class TestSoftDeleteBehavior:
    """Test soft-delete behavior and its effect on related objects."""

    def test_soft_delete_node_preserves_related(
        self,
        db,
        graph_version,
        node_template,
    ):
        """Soft-deleting a node preserves ports and edges in all_objects."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Soft Delete Node",
            config={},
            position={"x": 0, "y": 0},
        )
        port = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        port_id = port.id
        node_id = node.id

        # Soft-delete the node (model.delete() sets deleted=True)
        node.delete()

        # Node is soft-deleted: gone from no_workspace_objects, still in all_objects
        assert not Node.no_workspace_objects.filter(id=node_id).exists()
        assert Node.all_objects.filter(id=node_id).exists()

        # Port still exists in all_objects (soft delete doesn't cascade)
        assert Port.all_objects.filter(id=port_id).exists()
        # Port is also still visible in no_workspace_objects (not soft-deleted itself)
        assert Port.no_workspace_objects.filter(id=port_id).exists()


@pytest.mark.integration
class TestExecutionLifecycle:
    """Test the full execution lifecycle across GraphExecution → NodeExecution → ExecutionData."""

    def test_full_execution_creates_data_for_all_nodes(
        self,
        db,
        organization,
        workspace,
        user,
    ):
        """Build execution chain: GraphExecution → NodeExecution per node → ExecutionData per port."""
        # Build a graph with 2 nodes, each with an input and output port
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Execution Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="exec_template",
            display_name="Exec Template",
            description="For execution test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "integer"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )
        node_a = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Exec Node A",
            config={},
            position={"x": 0, "y": 0},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Exec Node B",
            config={},
            position={"x": 200, "y": 0},
        )

        # Create ports for both nodes
        port_a_in = Port.no_workspace_objects.create(
            node=node_a,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        port_a_out = Port.no_workspace_objects.create(
            node=node_a,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "integer"},
        )
        port_b_in = Port.no_workspace_objects.create(
            node=node_b,
            key="in",
            display_name="in",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        port_b_out = Port.no_workspace_objects.create(
            node=node_b,
            key="out",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "integer"},
        )

        # Create execution chain
        graph_exec = GraphExecution.no_workspace_objects.create(
            graph_version=version,
            status=GraphExecutionStatus.RUNNING,
            input_payload={"query": "test"},
        )
        node_exec_a = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_exec,
            node=node_a,
            status=NodeExecutionStatus.SUCCESS,
        )
        node_exec_b = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_exec,
            node=node_b,
            status=NodeExecutionStatus.SUCCESS,
        )

        # Create execution data for all ports
        data_a_in = ExecutionData.no_workspace_objects.create(
            node_execution=node_exec_a,
            port=port_a_in,
            payload="hello",
        )
        data_a_out = ExecutionData.no_workspace_objects.create(
            node_execution=node_exec_a,
            port=port_a_out,
            payload=42,
        )
        data_b_in = ExecutionData.no_workspace_objects.create(
            node_execution=node_exec_b,
            port=port_b_in,
            payload="world",
        )
        data_b_out = ExecutionData.no_workspace_objects.create(
            node_execution=node_exec_b,
            port=port_b_out,
            payload=99,
        )

        # Verify execution chain
        assert graph_exec.node_executions.count() == 2
        assert node_exec_a.execution_data.count() == 2
        assert node_exec_b.execution_data.count() == 2

        # Verify payload validation ran correctly
        assert data_a_in.is_valid is True  # "hello" matches {"type": "string"}
        assert data_a_out.is_valid is True  # 42 matches {"type": "integer"}
        assert data_b_in.is_valid is True
        assert data_b_out.is_valid is True

    def test_execution_data_denormalization_correct(
        self,
        db,
        organization,
        workspace,
        node_template,
        user,
    ):
        """Verify node field is auto-set from node_execution.node across all data."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Denorm Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )
        node = Node.no_workspace_objects.create(
            graph_version=version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Denorm Node",
            config={},
            position={"x": 0, "y": 0},
        )
        port_in = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        port_out = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        graph_exec = GraphExecution.no_workspace_objects.create(
            graph_version=version,
            status=GraphExecutionStatus.RUNNING,
        )
        node_exec = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_exec,
            node=node,
            status=NodeExecutionStatus.RUNNING,
        )

        # Create ExecutionData WITHOUT setting node — should be auto-denormalized
        data_in = ExecutionData.no_workspace_objects.create(
            node_execution=node_exec,
            port=port_in,
            payload="test input",
        )
        data_out = ExecutionData.no_workspace_objects.create(
            node_execution=node_exec,
            port=port_out,
            payload="test output",
        )

        # Both should have node auto-set from node_execution.node
        assert data_in.node == node
        assert data_out.node == node
        assert data_in.node_id == node.id
        assert data_out.node_id == node.id


@pytest.mark.integration
class TestSubgraphNodeIntegration:
    """Test subgraph node reference chains across graphs."""

    def test_graph_references_active_subgraph(self, db, organization, workspace, user):
        """Build a graph with a subgraph node referencing an active version of another graph."""
        # Create referenced graph with active version
        ref_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Referenced Graph",
            created_by=user,
        )
        ref_version = GraphVersion.no_workspace_objects.create(
            graph=ref_graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )

        # Create main graph
        main_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Main Graph",
            created_by=user,
        )
        main_version = GraphVersion.no_workspace_objects.create(
            graph=main_graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )

        # Create a node template for atomic nodes
        template = NodeTemplate.no_workspace_objects.create(
            name="subgraph_test_template",
            display_name="Subgraph Test Template",
            description="For subgraph integration test",
            categories=["test"],
            input_definition=[{"key": "in", "data_schema": {"type": "string"}}],
            output_definition=[{"key": "out", "data_schema": {"type": "string"}}],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )

        # Create an atomic node and a subgraph node in the main graph
        atomic_node = Node.no_workspace_objects.create(
            graph_version=main_version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Atomic Node",
            config={},
            position={"x": 0, "y": 0},
        )
        subgraph_node = Node.no_workspace_objects.create(
            graph_version=main_version,
            ref_graph_version=ref_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Node",
            config={},
            position={"x": 200, "y": 0},
        )

        # Validate passes
        subgraph_node.clean()

        # Verify reference chain
        assert subgraph_node.ref_graph_version == ref_version
        assert subgraph_node.ref_graph_version.graph == ref_graph
        assert subgraph_node.ref_graph_version.status == GraphVersionStatus.ACTIVE

        # Both nodes belong to the main version
        assert atomic_node.graph_version == main_version
        assert subgraph_node.graph_version == main_version
        assert main_version.nodes.count() == 2

    def test_version_promotion_workflow(self, db, organization, workspace, user):
        """DRAFT → ACTIVE promotion of referenced version, verify subgraph node still valid."""
        # Create referenced graph with v1 as active
        ref_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Promotable Graph",
            created_by=user,
        )
        v1 = GraphVersion.no_workspace_objects.create(
            graph=ref_graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )

        # Create main graph that references v1
        main_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Ref Graph",
            created_by=user,
        )
        main_version = GraphVersion.no_workspace_objects.create(
            graph=main_graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        subgraph_node = Node.no_workspace_objects.create(
            graph_version=main_version,
            ref_graph_version=v1,
            type=NodeType.SUBGRAPH,
            name="Subgraph Ref",
            config={},
            position={"x": 0, "y": 0},
        )

        # Subgraph node validates with v1 as active
        subgraph_node.clean()

        # Now create v2 as draft, then promote it to active (demoting v1)
        v2 = GraphVersion.no_workspace_objects.create(
            graph=ref_graph,
            version_number=2,
            status=GraphVersionStatus.DRAFT,
        )

        # Demote v1 to inactive, promote v2 to active
        v1.status = GraphVersionStatus.INACTIVE
        v1.save()
        v2.status = GraphVersionStatus.ACTIVE
        v2.clean()
        v2.save()

        # Verify v2 is now active, v1 is inactive
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.status == GraphVersionStatus.INACTIVE
        assert v2.status == GraphVersionStatus.ACTIVE

        # The subgraph node still references v1, which is now inactive
        # Validation should PASS because inactive versions are now allowed
        subgraph_node.refresh_from_db()
        subgraph_node.clean()  # Should not raise
        assert subgraph_node.ref_graph_version.status == GraphVersionStatus.INACTIVE


@pytest.mark.integration
class TestCrossGraphCycleIntegration:
    """Test cross-graph cycle detection in integration scenarios."""

    def test_cross_graph_cycle_rejected_in_save(
        self, db, organization, workspace, user
    ):
        """Cross-graph cycle is rejected when saving a subgraph node."""
        graph_a = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph A",
            created_by=user,
        )
        graph_b = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph B",
            created_by=user,
        )

        ver_a = GraphVersion.no_workspace_objects.create(
            graph=graph_a,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        active_a = GraphVersion.no_workspace_objects.create(
            graph=graph_a,
            version_number=2,
            status=GraphVersionStatus.ACTIVE,
        )
        ver_b = GraphVersion.no_workspace_objects.create(
            graph=graph_b,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        active_b = GraphVersion.no_workspace_objects.create(
            graph=graph_b,
            version_number=2,
            status=GraphVersionStatus.ACTIVE,
        )

        # A -> B
        Node.no_workspace_objects.create(
            graph_version=ver_a,
            ref_graph_version=active_b,
            type=NodeType.SUBGRAPH,
            name="A->B",
            config={},
        )

        # B -> A should fail on save
        with pytest.raises(ValidationError, match="circular dependency"):
            Node.no_workspace_objects.create(
                graph_version=ver_b,
                ref_graph_version=active_a,
                type=NodeType.SUBGRAPH,
                name="B->A",
                config={},
            )

    def test_template_reference_no_cycle(
        self, db, organization, workspace, user, active_template_graph_version
    ):
        """Referencing a template graph should not create cycles."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="User Graph",
            created_by=user,
        )
        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )

        # User graph references template - should succeed
        node = Node.no_workspace_objects.create(
            graph_version=version,
            ref_graph_version=active_template_graph_version,
            type=NodeType.SUBGRAPH,
            name="Template Ref",
            config={},
        )
        node.clean()  # Should not raise


@pytest.mark.integration
class TestGraphVersionPromotion:
    """Test graph version promotion with the active constraint when nodes/edges exist."""

    def test_only_one_active_version_with_nodes(
        self,
        db,
        organization,
        workspace,
        user,
    ):
        """Two versions with nodes; promote second; verify first is demoted."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Promotion Graph",
            created_by=user,
        )
        template = NodeTemplate.no_workspace_objects.create(
            name="promotion_template",
            display_name="Promotion Template",
            description="For promotion test",
            categories=["test"],
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={},
        )

        # Create v1 as active with nodes
        v1 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
        )
        node_v1 = Node.no_workspace_objects.create(
            graph_version=v1,
            node_template=template,
            type=NodeType.ATOMIC,
            name="V1 Node",
            config={},
            position={"x": 0, "y": 0},
        )
        Port.no_workspace_objects.create(
            node=node_v1,
            key="custom",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Create v2 as draft with nodes
        v2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.DRAFT,
        )
        node_v2 = Node.no_workspace_objects.create(
            graph_version=v2,
            node_template=template,
            type=NodeType.ATOMIC,
            name="V2 Node",
            config={},
            position={"x": 0, "y": 0},
        )
        Port.no_workspace_objects.create(
            node=node_v2,
            key="custom",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Trying to promote v2 while v1 is still active should fail
        v2.status = GraphVersionStatus.ACTIVE
        with pytest.raises(
            ValidationError, match="Only one active version allowed per graph"
        ):
            v2.clean()

        # Demote v1, then promote v2
        v1.status = GraphVersionStatus.INACTIVE
        v1.save()

        v2.status = GraphVersionStatus.ACTIVE
        v2.clean()  # Should pass now
        v2.save()

        # Verify final state
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.status == GraphVersionStatus.INACTIVE
        assert v2.status == GraphVersionStatus.ACTIVE

        # Both versions' nodes still exist independently
        assert v1.nodes.count() == 1
        assert v2.nodes.count() == 1
        assert v1.nodes.first().name == "V1 Node"
        assert v2.nodes.first().name == "V2 Node"

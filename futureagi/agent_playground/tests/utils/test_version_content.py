"""Tests for version content management utilities."""

import uuid

import pytest
from django.core.exceptions import ValidationError

from agent_playground.models.choices import (
    GraphVersionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.edge import Edge
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.utils.version_content import (
    create_edge,
    create_node,
    create_port,
    update_version_content,
)

# Use fixtures from conftest.py: node_template, graph, graph_version, etc.


@pytest.fixture
def dynamic_template(db):
    """Dynamic template for tests needing arbitrary port keys."""
    return NodeTemplate.no_workspace_objects.create(
        name="version_content_test_template",
        display_name="Version Content Test Template",
        description="Template for version content tests",
        categories=["testing"],
        input_definition=[],
        output_definition=[],
        input_mode=PortMode.DYNAMIC,
        output_mode=PortMode.DYNAMIC,
        config_schema={},
    )


@pytest.fixture
def strict_template(db):
    """Strict template with defined input/output ports."""
    return NodeTemplate.no_workspace_objects.create(
        name="vc_strict_template",
        display_name="VC Strict Template",
        description="Strict template for version content tests",
        categories=["testing"],
        input_definition=[
            {
                "key": "data_in",
                "display_name": "data_input",
                "data_schema": {"type": "string"},
            },
        ],
        output_definition=[
            {
                "key": "data_out",
                "display_name": "data_output",
                "data_schema": {"type": "string"},
            },
        ],
        input_mode=PortMode.STRICT,
        output_mode=PortMode.STRICT,
        config_schema={},
    )


class TestCreateNode:
    """Tests for create_node function."""

    def test_creates_atomic_node(self, graph_version, node_template):
        """Test creating an atomic node."""
        node_data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
            "config": {"param": "value"},
            "position": {"x": 100, "y": 200},
        }

        node = create_node(graph_version, node_data)

        assert node.id is not None
        assert node.graph_version == graph_version
        assert node.type == NodeType.ATOMIC
        assert node.name == "Test Node"
        assert node.node_template == node_template
        assert node.config == {"param": "value"}
        assert node.position == {"x": 100, "y": 200}

    def test_creates_node_with_defaults(self, graph_version, node_template):
        """Test creating a node with default config and position."""
        node_data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
        }

        node = create_node(graph_version, node_data)

        assert node.config == {}
        assert node.position == {}

    def test_creates_subgraph_node(
        self, graph_version, active_referenced_graph_version
    ):
        """Test creating a subgraph node."""
        node_data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
        }

        node = create_node(graph_version, node_data)

        assert node.type == NodeType.SUBGRAPH
        assert node.ref_graph_version == active_referenced_graph_version
        assert node.node_template is None


class TestCreatePort:
    """Tests for create_port function."""

    def test_creates_input_port(self, graph_version, node_template):
        """Test creating an input port."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Test Node",
            node_template=node_template,
        )

        port_data = {
            "id": str(uuid.uuid4()),
            "key": "input1",
            "display_name": "input1",
            "direction": PortDirection.INPUT,
            "data_schema": {"type": "string"},
            "required": True,
        }

        port = create_port(node, port_data)

        assert port.id is not None
        assert port.node == node
        assert port.key == "input1"
        assert port.direction == PortDirection.INPUT
        assert port.data_schema == {"type": "string"}
        assert port.required is True

    def test_creates_output_port(self, graph_version, node_template):
        """Test creating an output port."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Test Node",
            node_template=node_template,
        )

        port_data = {
            "id": str(uuid.uuid4()),
            "key": "output1",
            "display_name": "output1",
            "direction": PortDirection.OUTPUT,
            "required": False,
            "default_value": "default",
            "metadata": {"description": "Output port"},
        }

        port = create_port(node, port_data)

        assert port.direction == PortDirection.OUTPUT
        assert port.required is False
        assert port.default_value == "default"
        assert port.metadata == {"description": "Output port"}

    def test_creates_port_with_defaults(self, graph_version, node_template):
        """Test creating a port with default values."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Test Node",
            node_template=node_template,
        )

        port_data = {
            "id": str(uuid.uuid4()),
            "key": "input1",
            "display_name": "input1",
            "direction": PortDirection.INPUT,
        }

        port = create_port(node, port_data)

        assert port.data_schema == {}
        assert port.required is True
        assert port.default_value is None
        assert port.metadata == {}
        assert port.ref_port is None

    def test_creates_port_with_ref_port(
        self, graph_version, active_referenced_graph_version, node_template
    ):
        """Test creating a subgraph port with ref_port_id."""
        child_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            type=NodeType.ATOMIC,
            name="Child Node",
            node_template=node_template,
        )
        child_port = Port.no_workspace_objects.create(
            node=child_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        subgraph_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Node",
            ref_graph_version=active_referenced_graph_version,
        )

        port_data = {
            "id": str(uuid.uuid4()),
            "key": "custom",
            "display_name": "My Input",
            "direction": PortDirection.INPUT,
            "ref_port_id": str(child_port.id),
        }

        port = create_port(subgraph_node, port_data)

        assert port.ref_port == child_port
        assert port.ref_port_id == child_port.id

    def test_creates_port_with_invalid_ref_port_id_raises(
        self, graph_version, active_referenced_graph_version
    ):
        """Test that create_port with nonexistent ref_port_id raises ValidationError."""
        subgraph_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Node",
            ref_graph_version=active_referenced_graph_version,
        )

        port_data = {
            "id": str(uuid.uuid4()),
            "key": "custom",
            "display_name": "My Input",
            "direction": PortDirection.INPUT,
            "ref_port_id": str(uuid.uuid4()),
        }

        with pytest.raises(ValidationError, match="not found"):
            create_port(subgraph_node, port_data)


class TestCreateEdge:
    """Tests for create_edge function."""

    def test_creates_edge_between_ports(self, graph_version, node_template):
        """Test creating an edge between two ports."""
        node1 = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node 1",
            node_template=node_template,
        )
        node2 = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node 2",
            node_template=node_template,
        )

        output_port = Port.no_workspace_objects.create(
            node=node1,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )
        input_port = Port.no_workspace_objects.create(
            node=node2,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node1, target_node=node2
        )
        edge = create_edge(graph_version, output_port.id, input_port.id)

        assert edge.id is not None
        assert edge.graph_version == graph_version
        assert edge.source_port == output_port
        assert edge.target_port == input_port


class TestUpdateVersionContent:
    """Tests for update_version_content function."""

    def test_creates_nodes_with_auto_ports_from_template(
        self, graph, graph_version, strict_template
    ):
        """Test creating nodes with auto-created ports from strict template definitions."""
        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(strict_template.id),
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
        )

        nodes = Node.no_workspace_objects.filter(graph_version=graph_version)
        assert nodes.count() == 1

        ports = Port.no_workspace_objects.filter(node__graph_version=graph_version)
        assert ports.count() == 2
        port_info = {(p.display_name, p.direction) for p in ports}
        assert ("data_input", PortDirection.INPUT) in port_info
        assert ("data_output", PortDirection.OUTPUT) in port_info

    def test_auto_creates_edges_by_name_matching(self, graph, graph_version, db):
        """Test that edges are auto-created by matching display_names across connections."""
        # Use STRICT templates so ports are created from template definitions
        producer = NodeTemplate.no_workspace_objects.create(
            name="vc_edge_producer",
            display_name="Edge Producer",
            description="test",
            categories=["test"],
            input_definition=[],
            output_definition=[
                {
                    "key": "out1",
                    "display_name": "data",
                    "data_schema": {"type": "string"},
                },
            ],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )
        consumer = NodeTemplate.no_workspace_objects.create(
            name="vc_edge_consumer",
            display_name="Edge Consumer",
            description="test",
            categories=["test"],
            input_definition=[
                {
                    "key": "in1",
                    "display_name": "data",
                    "data_schema": {"type": "string"},
                },
            ],
            output_definition=[],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )

        node_1_id = str(uuid.uuid4())
        node_2_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": node_1_id,
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(producer.id),
            },
            {
                "id": node_2_id,
                "type": NodeType.ATOMIC,
                "name": "Node 2",
                "node_template_id": str(consumer.id),
            },
        ]
        node_connections_data = [
            {
                "source_node_id": node_1_id,
                "target_node_id": node_2_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
        )

        # producer output "data" matches consumer input "data" → 1 edge
        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 1

    def test_auto_creates_code_execution_input_edge(self, graph, graph_version, db):
        producer = NodeTemplate.no_workspace_objects.create(
            name="vc_code_source",
            display_name="Code Source",
            description="test",
            categories=["test"],
            input_definition=[],
            output_definition=[
                {
                    "key": "response",
                    "display_name": "response",
                    "data_schema": {"type": "object"},
                },
            ],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={},
        )
        code_template, _ = NodeTemplate.no_workspace_objects.get_or_create(
            name="code_execution",
            defaults={
                "display_name": "Code Execution",
                "description": "test",
                "categories": ["code"],
                "input_definition": [
                    {
                        "key": "inputs",
                        "display_name": "inputs",
                        "data_schema": {"type": "object"},
                        "required": False,
                    },
                ],
                "output_definition": [],
                "input_mode": PortMode.STRICT,
                "output_mode": PortMode.STRICT,
                "config_schema": {},
            },
        )
        source_id = str(uuid.uuid4())
        code_id = str(uuid.uuid4())

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=[
                {
                    "id": source_id,
                    "type": NodeType.ATOMIC,
                    "name": "source",
                    "node_template_id": str(producer.id),
                },
                {
                    "id": code_id,
                    "type": NodeType.ATOMIC,
                    "name": "code",
                    "node_template_id": str(code_template.id),
                    "config": {"language": "python", "code": "result = inputs"},
                },
            ],
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=[
                {"source_node_id": source_id, "target_node_id": code_id}
            ],
        )

        edge = Edge.no_workspace_objects.get(graph_version=graph_version)
        assert edge.source_port.display_name == "response"
        assert edge.target_port.display_name == "inputs"

    def test_no_edges_when_names_dont_match(self, graph, graph_version, db):
        """Test that no edges are created when display_names don't match."""
        producer = NodeTemplate.no_workspace_objects.create(
            name="vc_producer",
            display_name="Producer",
            description="test",
            categories=["test"],
            input_definition=[],
            output_definition=[
                {
                    "key": "out1",
                    "display_name": "alpha",
                    "data_schema": {"type": "string"},
                },
            ],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.STRICT,
            config_schema={},
        )
        consumer = NodeTemplate.no_workspace_objects.create(
            name="vc_consumer",
            display_name="Consumer",
            description="test",
            categories=["test"],
            input_definition=[
                {
                    "key": "in1",
                    "display_name": "beta",
                    "data_schema": {"type": "string"},
                },
            ],
            output_definition=[],
            input_mode=PortMode.STRICT,
            output_mode=PortMode.DYNAMIC,
            config_schema={},
        )

        node_1_id = str(uuid.uuid4())
        node_2_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": node_1_id,
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(producer.id),
            },
            {
                "id": node_2_id,
                "type": NodeType.ATOMIC,
                "name": "Node 2",
                "node_template_id": str(consumer.id),
            },
        ]
        node_connections_data = [
            {
                "source_node_id": node_1_id,
                "target_node_id": node_2_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
        )

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0

    def test_replaces_existing_content(self, graph, graph_version, dynamic_template):
        """Test that update replaces existing content."""
        # Create initial content
        node1 = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Old Node",
            node_template=dynamic_template,
        )
        Port.no_workspace_objects.create(
            node=node1,
            key="custom",
            display_name="old_port",
            direction=PortDirection.OUTPUT,
        )

        # Update with new content
        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "New Node",
                "node_template_id": str(dynamic_template.id),
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
        )

        # Verify old content is gone
        nodes = Node.no_workspace_objects.filter(graph_version=graph_version)
        assert nodes.count() == 1
        assert nodes.first().name == "New Node"

    def test_promotes_version_to_active(self, graph, graph_version, strict_template):
        """Test that setting status to active promotes the version."""
        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(strict_template.id),
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.ACTIVE,
            commit_message="Publishing version",
        )

        graph_version.refresh_from_db()
        assert graph_version.status == GraphVersionStatus.ACTIVE
        assert graph_version.commit_message == "Publishing version"

    def test_deactivates_previous_active_version(
        self, graph, graph_version, strict_template
    ):
        """Test that promoting a version deactivates the previous active version."""
        graph_version.status = GraphVersionStatus.ACTIVE
        graph_version.save()

        new_version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.DRAFT,
        )

        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(strict_template.id),
            },
        ]

        update_version_content(
            graph=graph,
            version=new_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.ACTIVE,
            commit_message="New active version",
        )

        graph_version.refresh_from_db()
        assert graph_version.status == GraphVersionStatus.INACTIVE

        new_version.refresh_from_db()
        assert new_version.status == GraphVersionStatus.ACTIVE

    def test_saves_commit_message_for_draft(
        self, graph, graph_version, dynamic_template
    ):
        """Test that commit message is saved even for draft saves."""
        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(dynamic_template.id),
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message="Work in progress",
        )

        graph_version.refresh_from_db()
        assert graph_version.status == GraphVersionStatus.DRAFT
        assert graph_version.commit_message == "Work in progress"

    def test_creates_subgraph_node_with_input_mappings(
        self, graph, graph_version, active_referenced_graph_version, dynamic_template
    ):
        """Test creating a subgraph node with input_mappings creates input ports."""
        atomic_node_id = str(uuid.uuid4())
        subgraph_node_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": atomic_node_id,
                "type": NodeType.ATOMIC,
                "name": "Source Node",
                "node_template_id": str(dynamic_template.id),
            },
            {
                "id": subgraph_node_id,
                "type": NodeType.SUBGRAPH,
                "name": "Subgraph Node",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [
                    {
                        "id": str(uuid.uuid4()),
                        "key": "custom",
                        "display_name": "summary",
                        "direction": PortDirection.OUTPUT,
                        "data_schema": {"type": "string"},
                    },
                ],
                "input_mappings": [
                    {"key": "context", "value": "Source Node.response"},
                    {"key": "question", "value": None},
                ],
            },
        ]
        node_connections_data = [
            {
                "source_node_id": atomic_node_id,
                "target_node_id": subgraph_node_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
        )

        nodes = Node.no_workspace_objects.filter(graph_version=graph_version)
        assert nodes.count() == 2

        subgraph_node = nodes.get(type=NodeType.SUBGRAPH)
        subgraph_ports = Port.no_workspace_objects.filter(node=subgraph_node)
        assert subgraph_ports.count() == 3  # 1 output + 2 input from mappings

        port_info = {(p.display_name, p.direction) for p in subgraph_ports}
        assert ("summary", PortDirection.OUTPUT) in port_info
        assert ("context", PortDirection.INPUT) in port_info
        assert ("question", PortDirection.INPUT) in port_info

    def test_subgraph_input_mappings_create_edges(
        self, graph, graph_version, active_referenced_graph_version, dynamic_template
    ):
        """Test that non-null input_mappings values create edges from source node."""
        # Source node needs an output port named "response" for the mapping to resolve
        source_node_id = str(uuid.uuid4())
        subgraph_node_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": source_node_id,
                "type": NodeType.ATOMIC,
                "name": "Source Node",
                "node_template_id": str(dynamic_template.id),
                # Dynamic template: ports come from prompt_template. Let's add manually.
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello {{name}}"}],
                        }
                    ],
                    "response_format": "text",
                },
            },
            {
                "id": subgraph_node_id,
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [],
                "input_mappings": [
                    {"key": "context", "value": "Source Node.response"},
                ],
            },
        ]
        node_connections_data = [
            {
                "source_node_id": source_node_id,
                "target_node_id": subgraph_node_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
        )

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 1

        edge = edges.first()
        assert edge.source_port.display_name == "response"
        assert edge.target_port.display_name == "context"

    def test_subgraph_null_mapping_creates_no_edge(
        self, graph, graph_version, active_referenced_graph_version, dynamic_template
    ):
        """Test that null input_mappings values create no edge."""
        source_node_id = str(uuid.uuid4())
        subgraph_node_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": source_node_id,
                "type": NodeType.ATOMIC,
                "name": "Source Node",
                "node_template_id": str(dynamic_template.id),
            },
            {
                "id": subgraph_node_id,
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [],
                "input_mappings": [
                    {"key": "question", "value": None},
                ],
            },
        ]
        node_connections_data = [
            {
                "source_node_id": source_node_id,
                "target_node_id": subgraph_node_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
        )

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0

    def test_llm_node_creates_ptn_and_ports(
        self, graph, graph_version, user, organization, workspace, llm_node_template
    ):
        """Test that LLM prompt node creates PTN + ports from {{variables}}."""
        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "LLM Node",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Summarize {{text}} for {{audience}}",
                                }
                            ],
                        },
                    ],
                    "response_format": "text",
                    "model": "gpt-4o",
                },
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        node = Node.no_workspace_objects.filter(graph_version=graph_version).first()
        assert node is not None

        # Check ports
        ports = Port.no_workspace_objects.filter(node=node)
        port_names = {p.display_name for p in ports}
        assert "text" in port_names
        assert "audience" in port_names
        assert "response" in port_names
        assert ports.count() == 3

        # Check PTN was created
        ptn = PromptTemplateNode.no_workspace_objects.filter(node=node).first()
        assert ptn is not None
        assert ptn.prompt_template is not None
        assert ptn.prompt_version is not None

    def test_template_node_creates_ports_from_definition(
        self, graph, graph_version, strict_template
    ):
        """Test that strict/extensible template auto-creates ports from definitions."""
        nodes_data = [
            {
                "id": str(uuid.uuid4()),
                "type": NodeType.ATOMIC,
                "name": "Strict Node",
                "node_template_id": str(strict_template.id),
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
        )

        node = Node.no_workspace_objects.filter(graph_version=graph_version).first()
        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 2

        port_info = {(p.key, p.display_name, p.direction) for p in ports}
        assert ("data_in", "data_input", PortDirection.INPUT) in port_info
        assert ("data_out", "data_output", PortDirection.OUTPUT) in port_info

    def test_empty_nodes_and_edges(self, graph, graph_version):
        """Test update with empty nodes list."""
        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=[],
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
        )

        nodes = Node.no_workspace_objects.filter(graph_version=graph_version)
        assert nodes.count() == 0

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0

    def test_update_clears_existing_content_even_with_empty_data(
        self, graph, graph_version, dynamic_template
    ):
        """Test that updating with empty data clears existing content."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Existing Node",
            node_template=dynamic_template,
        )
        Port.no_workspace_objects.create(
            node=node,
            key="custom",
            display_name="existing_port",
            direction=PortDirection.OUTPUT,
        )

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=[],
            new_status=GraphVersionStatus.DRAFT,
            commit_message="Cleared all content",
        )

        nodes = Node.no_workspace_objects.filter(graph_version=graph_version)
        assert nodes.count() == 0

        ports = Port.no_workspace_objects.filter(node__graph_version=graph_version)
        assert ports.count() == 0

    def test_raises_on_invalid_node_connection_temp_id(
        self, graph, graph_version, dynamic_template
    ):
        """Test that invalid temp_id in node_connections raises ValidationError."""
        node_1_id = str(uuid.uuid4())
        nonexistent_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": node_1_id,
                "type": NodeType.ATOMIC,
                "name": "Node 1",
                "node_template_id": str(dynamic_template.id),
            },
        ]
        node_connections_data = [
            {
                "source_node_id": node_1_id,
                "target_node_id": nonexistent_id,
            },
        ]

        with pytest.raises(ValidationError, match=nonexistent_id):
            update_version_content(
                graph=graph,
                version=graph_version,
                nodes_data=nodes_data,
                new_status=GraphVersionStatus.DRAFT,
                commit_message=None,
                node_connections_data=node_connections_data,
            )

    def test_dot_notation_edge_auto_creation(
        self, graph, graph_version, user, organization, workspace, llm_node_template
    ):
        """Test that dot-notation input ports create edges to the correct source port."""
        producer_id = str(uuid.uuid4())
        consumer_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": producer_id,
                "type": NodeType.ATOMIC,
                "name": "Producer",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello {{name}}"}],
                        }
                    ],
                    "response_format": "text",
                },
            },
            {
                "id": consumer_id,
                "type": NodeType.ATOMIC,
                "name": "Consumer",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-1",
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Summarize {{Producer.response}}",
                                }
                            ],
                        }
                    ],
                    "response_format": "text",
                },
            },
        ]
        node_connections_data = [
            {
                "source_node_id": producer_id,
                "target_node_id": consumer_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 1
        edge = edges.first()
        assert edge.source_port.display_name == "response"
        assert edge.target_port.display_name == "Producer.response"

    def test_dot_notation_with_extraction_path_creates_edge(
        self, graph, graph_version, user, organization, workspace, llm_node_template
    ):
        """Test that deep dot-notation (with extraction path) still creates the edge."""
        producer_id = str(uuid.uuid4())
        consumer_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": producer_id,
                "type": NodeType.ATOMIC,
                "name": "Producer",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello {{name}}"}],
                        }
                    ],
                    "response_format": "json",
                },
            },
            {
                "id": consumer_id,
                "type": NodeType.ATOMIC,
                "name": "Consumer",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-1",
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Use {{Producer.response.data.name}}",
                                }
                            ],
                        }
                    ],
                    "response_format": "text",
                },
            },
        ]
        node_connections_data = [
            {
                "source_node_id": producer_id,
                "target_node_id": consumer_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 1
        edge = edges.first()
        assert edge.source_port.display_name == "response"
        assert edge.target_port.display_name == "Producer.response.data.name"

    def test_dot_notation_no_edge_when_source_name_doesnt_match(
        self, graph, graph_version, user, organization, workspace, llm_node_template
    ):
        """Test that dot-notation doesn't create an edge when source node name differs."""
        producer_id = str(uuid.uuid4())
        consumer_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": producer_id,
                "type": NodeType.ATOMIC,
                "name": "Producer",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello {{name}}"}],
                        }
                    ],
                    "response_format": "text",
                },
            },
            {
                "id": consumer_id,
                "type": NodeType.ATOMIC,
                "name": "Consumer",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-1",
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Use {{OtherNode.response}}"}
                            ],
                        }
                    ],
                    "response_format": "text",
                },
            },
        ]
        node_connections_data = [
            {
                "source_node_id": producer_id,
                "target_node_id": consumer_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0


class TestInputMappingsEndToEnd:
    """End-to-end tests verifying input_mappings round-trip through serializer.

    These tests call update_version_content() then serialize via
    prefetch_version_detail + GraphVersionDetailSerializer to verify
    that input_mappings values are correctly reconstructed (not null).
    """

    def test_input_mappings_roundtrip_with_values(
        self,
        graph,
        graph_version,
        active_referenced_graph_version,
        dynamic_template,
        user,
        organization,
        workspace,
        llm_node_template,
    ):
        """Test that input_mappings with non-null values are correctly
        reconstructed in the serialized response."""
        from agent_playground.serializers.graph_version import (
            GraphVersionDetailSerializer,
            prefetch_version_detail,
        )

        source_node_id = str(uuid.uuid4())
        subgraph_node_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": source_node_id,
                "type": NodeType.ATOMIC,
                "name": "Source Node",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello {{name}}"}],
                        }
                    ],
                    "response_format": "text",
                },
            },
            {
                "id": subgraph_node_id,
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [
                    {
                        "id": str(uuid.uuid4()),
                        "key": "custom",
                        "display_name": "summary",
                        "direction": PortDirection.OUTPUT,
                        "data_schema": {"type": "string"},
                    },
                ],
                "input_mappings": [
                    {"key": "context", "value": "Source Node.response"},
                    {"key": "question", "value": None},
                ],
            },
        ]
        node_connections_data = [
            {
                "source_node_id": source_node_id,
                "target_node_id": subgraph_node_id,
            },
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        # Serialize via the same path as the API view
        prefetched = prefetch_version_detail(graph_version)
        data = GraphVersionDetailSerializer(prefetched).data

        # Find the subgraph node in the response
        sub_node_data = next(n for n in data["nodes"] if n["type"] == NodeType.SUBGRAPH)

        assert sub_node_data["input_mappings"] is not None
        mappings = {m["key"]: m["value"] for m in sub_node_data["input_mappings"]}
        assert mappings["context"] == "Source Node.response"
        assert mappings["question"] is None

    def test_input_mappings_all_null_values(
        self,
        graph,
        graph_version,
        active_referenced_graph_version,
        dynamic_template,
    ):
        """Test that all-null input_mappings (globally exposed inputs)
        serialize correctly."""
        from agent_playground.serializers.graph_version import (
            GraphVersionDetailSerializer,
            prefetch_version_detail,
        )

        source_id = str(uuid.uuid4())
        subgraph_id = str(uuid.uuid4())

        nodes_data = [
            {
                "id": source_id,
                "type": NodeType.ATOMIC,
                "name": "Source",
                "node_template_id": str(dynamic_template.id),
            },
            {
                "id": subgraph_id,
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [],
                "input_mappings": [
                    {"key": "field_a", "value": None},
                    {"key": "field_b", "value": None},
                ],
            },
        ]
        node_connections_data = [
            {"source_node_id": source_id, "target_node_id": subgraph_id},
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=node_connections_data,
        )

        prefetched = prefetch_version_detail(graph_version)
        data = GraphVersionDetailSerializer(prefetched).data

        sub_node_data = next(n for n in data["nodes"] if n["type"] == NodeType.SUBGRAPH)
        mappings = {m["key"]: m["value"] for m in sub_node_data["input_mappings"]}
        assert mappings["field_a"] is None
        assert mappings["field_b"] is None

    def test_input_mappings_update_replaces_previous(
        self,
        graph,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
        llm_node_template,
    ):
        """Test that calling update_version_content again replaces old
        input_mappings with new ones."""
        from agent_playground.serializers.graph_version import (
            GraphVersionDetailSerializer,
            prefetch_version_detail,
        )

        source_id = str(uuid.uuid4())
        subgraph_id = str(uuid.uuid4())

        # First save — map context to Source.response
        nodes_data = [
            {
                "id": source_id,
                "type": NodeType.ATOMIC,
                "name": "Source",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{q}}"}],
                        }
                    ],
                    "response_format": "text",
                },
            },
            {
                "id": subgraph_id,
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [],
                "input_mappings": [
                    {"key": "context", "value": "Source.response"},
                ],
            },
        ]
        nc_data = [
            {"source_node_id": source_id, "target_node_id": subgraph_id},
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=nc_data,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        # Second save — change mapping to null (globally exposed)
        source_id2 = str(uuid.uuid4())
        subgraph_id2 = str(uuid.uuid4())

        nodes_data2 = [
            {
                "id": source_id2,
                "type": NodeType.ATOMIC,
                "name": "Source",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{q}}"}],
                        }
                    ],
                    "response_format": "text",
                },
            },
            {
                "id": subgraph_id2,
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "ports": [],
                "input_mappings": [
                    {"key": "context", "value": None},
                ],
            },
        ]
        nc_data2 = [
            {"source_node_id": source_id2, "target_node_id": subgraph_id2},
        ]

        update_version_content(
            graph=graph,
            version=graph_version,
            nodes_data=nodes_data2,
            new_status=GraphVersionStatus.DRAFT,
            commit_message=None,
            node_connections_data=nc_data2,
            user=user,
            organization=organization,
            workspace=workspace,
        )

        prefetched = prefetch_version_detail(graph_version)
        data = GraphVersionDetailSerializer(prefetched).data

        sub_node_data = next(n for n in data["nodes"] if n["type"] == NodeType.SUBGRAPH)
        mappings = {m["key"]: m["value"] for m in sub_node_data["input_mappings"]}
        # After second save, context should be null (no edge)
        assert mappings["context"] is None

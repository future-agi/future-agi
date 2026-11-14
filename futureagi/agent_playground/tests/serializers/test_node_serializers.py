"""Comprehensive tests for node serializers."""

import uuid
from unittest.mock import PropertyMock

import pytest

from agent_playground.models.choices import NodeType, PortDirection
from agent_playground.models.edge import Edge
from agent_playground.models.node import Node
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.serializers.node import (
    CreateNodeSerializer,
    NodeReadSerializer,
    NodeWriteSerializer,
    PromptTemplateDataSerializer,
    UpdateNodeSerializer,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion


class TestNodeReadSerializer:
    """Tests for NodeReadSerializer."""

    # ==================== Atomic Node Tests ====================

    def test_serializes_atomic_node_basic(self, node):
        """Test basic serialization of atomic node."""
        serializer = NodeReadSerializer(node)
        data = serializer.data

        assert data["id"] == str(node.id)
        assert data["type"] == NodeType.ATOMIC
        assert data["name"] == node.name
        assert data["config"] == node.config
        assert data["position"] == node.position

    def test_serializes_atomic_node_with_template(self, node, node_template):
        """Test that atomic node includes template ID."""
        serializer = NodeReadSerializer(node)
        data = serializer.data

        assert data["node_template_id"] == str(node_template.id)
        assert data["ref_graph_version_id"] is None

    def test_serializes_atomic_node_with_ports(self, node, input_port, output_port):
        """Test serialization of atomic node with ports."""
        serializer = NodeReadSerializer(node)
        data = serializer.data

        assert len(data["ports"]) == 2
        port_keys = {p["key"] for p in data["ports"]}
        assert "input1" in port_keys
        assert "output1" in port_keys

    def test_serializes_atomic_node_with_config(self, graph_version, node_template):
        """Test serialization of atomic node with config."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Configured Node",
            config={"model": "gpt-4", "temperature": 0.7, "max_tokens": 1000},
            position={"x": 100, "y": 200},
        )
        serializer = NodeReadSerializer(node)
        data = serializer.data

        assert data["config"]["model"] == "gpt-4"
        assert data["config"]["temperature"] == 0.7
        assert data["config"]["max_tokens"] == 1000

    def test_serializes_atomic_node_with_position(self, graph_version, node_template):
        """Test serialization of atomic node with position."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Positioned Node",
            config={},
            position={"x": 500, "y": 300, "z": 10},
        )
        serializer = NodeReadSerializer(node)
        data = serializer.data

        assert data["position"]["x"] == 500
        assert data["position"]["y"] == 300
        assert data["position"]["z"] == 10

    # ==================== Subgraph Node Tests ====================

    def test_serializes_subgraph_node_basic(self, subgraph_node):
        """Test basic serialization of subgraph node."""
        serializer = NodeReadSerializer(subgraph_node)
        data = serializer.data

        assert data["type"] == NodeType.SUBGRAPH
        assert data["name"] == subgraph_node.name
        assert data["node_template_id"] is None

    def test_serializes_subgraph_node_with_ref(
        self, subgraph_node, active_referenced_graph_version
    ):
        """Test that subgraph node includes reference version ID."""
        serializer = NodeReadSerializer(subgraph_node)
        data = serializer.data

        assert data["ref_graph_version_id"] == str(active_referenced_graph_version.id)
        assert data["node_template_id"] is None

    def test_serializes_subgraph_node_empty_config(self, subgraph_node):
        """Test that subgraph node has empty config."""
        serializer = NodeReadSerializer(subgraph_node)
        data = serializer.data

        assert data["config"] == {}

    # ==================== Port Nesting Tests ====================

    def test_nested_ports_serialized_correctly(self, node, input_port, output_port):
        """Test that nested ports are properly serialized."""
        serializer = NodeReadSerializer(node)
        data = serializer.data

        ports = data["ports"]
        port_directions = {p["direction"] for p in ports}
        assert PortDirection.INPUT in port_directions
        assert PortDirection.OUTPUT in port_directions

        # Check port structure
        for port in ports:
            assert "id" in port
            assert "key" in port
            assert "direction" in port
            assert "data_schema" in port
            assert "required" in port

    def test_node_with_many_ports(self, dynamic_node):
        """Test node with multiple ports."""
        # Create additional ports on dynamic node (allows key="custom")
        for i in range(5):
            Port.no_workspace_objects.create(
                node=dynamic_node,
                key="custom",
                display_name=f"port_{i}",
                direction=PortDirection.INPUT if i % 2 == 0 else PortDirection.OUTPUT,
                data_schema={"type": "string"},
            )

        serializer = NodeReadSerializer(dynamic_node)
        data = serializer.data

        assert len(data["ports"]) >= 5

    # ==================== Multiple Nodes Tests ====================

    def test_serializes_many_nodes(self, node, second_node):
        """Test serialization of multiple nodes."""
        serializer = NodeReadSerializer([node, second_node], many=True)
        data = serializer.data

        assert len(data) == 2
        names = {n["name"] for n in data}
        assert "Test Node" in names
        assert "Second Node" in names

    def test_all_fields_are_read_only(self):
        """Test that all fields in NodeReadSerializer are read-only."""
        serializer = NodeReadSerializer()
        for field_name in serializer.fields:
            assert serializer.fields[field_name].read_only


@pytest.mark.unit
class TestNodeReadSerializerPromptTemplate:
    """Tests for NodeReadSerializer.get_prompt_template — new fields."""

    def test_prompt_template_with_configuration_nesting(
        self,
        db,
        graph_version,
        node_template,
        organization,
        workspace,
    ):
        """get_prompt_template reads config fields from configuration sub-dict."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="LLM Node",
            config={},
            position={},
        )
        pt = PromptTemplate.no_workspace_objects.create(
            name="PT1", organization=organization, workspace=workspace
        )
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="v1",
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": "hi"}],
                "configuration": {
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "max_tokens": 512,
                    "top_p": 0.9,
                    "frequency_penalty": 0.1,
                    "presence_penalty": 0.2,
                    "response_format": "json",
                    "output_format": "markdown",
                    "tools": [{"type": "function", "function": {"name": "search"}}],
                    "tool_choice": "auto",
                    "model_detail": {"provider": "openai"},
                },
            },
            variable_names={"name": ["Alice"]},
            metadata={"source": "test"},
            is_draft=True,
        )
        PromptTemplateNode.no_workspace_objects.create(
            node=node, prompt_template=pt, prompt_version=pv
        )
        # Re-fetch with select_related
        node = Node.no_workspace_objects.select_related(
            "prompt_template_node__prompt_template",
            "prompt_template_node__prompt_version",
        ).get(pk=node.pk)

        serializer = NodeReadSerializer(node)
        result = serializer.data["prompt_template"]

        assert result["model"] == "gpt-4o"
        assert result["temperature"] == 0.7
        assert result["max_tokens"] == 512
        assert result["top_p"] == 0.9
        assert result["frequency_penalty"] == 0.1
        assert result["presence_penalty"] == 0.2
        assert result["response_format"] == "json"
        assert result["output_format"] == "markdown"
        assert result["tools"][0]["function"]["name"] == "search"
        assert result["tool_choice"] == "auto"
        assert result["model_detail"]["provider"] == "openai"
        assert result["variable_names"] == {"name": ["Alice"]}
        assert result["metadata"] == {"source": "test"}
        assert result["is_draft"] is True
        assert result["template_version"] == "v1"

    def test_prompt_template_with_legacy_flat_snapshot(
        self,
        db,
        graph_version,
        node_template,
        organization,
        workspace,
    ):
        """get_prompt_template falls back to flat snapshot when configuration missing."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Legacy Node",
            config={},
            position={},
        )
        pt = PromptTemplate.no_workspace_objects.create(
            name="PT Legacy", organization=organization, workspace=workspace
        )
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="v1",
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "gpt-3.5-turbo",
                "temperature": 0.5,
                "response_format": "text",
            },
            is_draft=False,
        )
        PromptTemplateNode.no_workspace_objects.create(
            node=node, prompt_template=pt, prompt_version=pv
        )
        node = Node.no_workspace_objects.select_related(
            "prompt_template_node__prompt_template",
            "prompt_template_node__prompt_version",
        ).get(pk=node.pk)

        serializer = NodeReadSerializer(node)
        result = serializer.data["prompt_template"]

        assert result["model"] == "gpt-3.5-turbo"
        assert result["temperature"] == 0.5
        assert result["response_format"] == "text"
        assert result["is_draft"] is False

    def test_prompt_template_with_legacy_list_snapshot(
        self,
        db,
        graph_version,
        node_template,
        organization,
        workspace,
    ):
        """get_prompt_template normalises list snapshot to dict.

        Since PromptTemplateNode.save() calls _update_output_port_schema()
        which can't handle list snapshots, we bypass save validation to test
        the serializer's list normalization path.
        """
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Legacy List Node",
            config={},
            position={},
        )
        pt = PromptTemplate.no_workspace_objects.create(
            name="PT List", organization=organization, workspace=workspace
        )
        # Create with dict snapshot first, then update to list via raw SQL
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="v1",
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "gpt-4",
                "configuration": {"response_format": "text"},
            },
            is_draft=True,
        )
        ptn = PromptTemplateNode.no_workspace_objects.create(
            node=node, prompt_template=pt, prompt_version=pv
        )
        # Simulate legacy list format by updating directly
        PromptVersion.no_workspace_objects.filter(pk=pv.pk).update(
            prompt_config_snapshot=[
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "model": "gpt-4",
                    "response_format": "text",
                }
            ]
        )

        node = Node.no_workspace_objects.select_related(
            "prompt_template_node__prompt_template",
            "prompt_template_node__prompt_version",
        ).get(pk=node.pk)

        serializer = NodeReadSerializer(node)
        result = serializer.data["prompt_template"]

        assert result["model"] == "gpt-4"
        assert result["messages"] == [{"role": "user", "content": "hi"}]

    def test_prompt_template_with_empty_list_snapshot(
        self,
        db,
        graph_version,
        node_template,
        organization,
        workspace,
    ):
        """get_prompt_template normalises empty list snapshot to empty dict."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Empty List Node",
            config={},
            position={},
        )
        pt = PromptTemplate.no_workspace_objects.create(
            name="PT Empty", organization=organization, workspace=workspace
        )
        # Create with dict snapshot, then update to empty list via raw SQL
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="v1",
            prompt_config_snapshot={
                "messages": [],
                "configuration": {"response_format": "text"},
            },
            is_draft=True,
        )
        ptn = PromptTemplateNode.no_workspace_objects.create(
            node=node, prompt_template=pt, prompt_version=pv
        )
        # Simulate legacy empty list format
        PromptVersion.no_workspace_objects.filter(pk=pv.pk).update(
            prompt_config_snapshot=[]
        )

        node = Node.no_workspace_objects.select_related(
            "prompt_template_node__prompt_template",
            "prompt_template_node__prompt_version",
        ).get(pk=node.pk)

        serializer = NodeReadSerializer(node)
        result = serializer.data["prompt_template"]

        assert result["messages"] == []
        assert result["response_format"] == "text"

    def test_prompt_template_none_when_no_ptn(self, node):
        """get_prompt_template returns None when node has no PTN."""
        serializer = NodeReadSerializer(node)
        assert serializer.data["prompt_template"] is None

    def test_prompt_template_configuration_takes_precedence_over_flat(
        self,
        db,
        graph_version,
        node_template,
        organization,
        workspace,
    ):
        """When both configuration sub-dict and flat keys exist, configuration wins."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Mixed Node",
            config={},
            position={},
        )
        pt = PromptTemplate.no_workspace_objects.create(
            name="PT Mixed", organization=organization, workspace=workspace
        )
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="v1",
            prompt_config_snapshot={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "old-model",  # flat key (legacy)
                "configuration": {"model": "new-model"},  # nested (wins)
            },
            is_draft=True,
        )
        PromptTemplateNode.no_workspace_objects.create(
            node=node, prompt_template=pt, prompt_version=pv
        )
        node = Node.no_workspace_objects.select_related(
            "prompt_template_node__prompt_template",
            "prompt_template_node__prompt_version",
        ).get(pk=node.pk)

        serializer = NodeReadSerializer(node)
        result = serializer.data["prompt_template"]

        assert result["model"] == "new-model"

    def test_node_connection_returned_via_context(
        self, db, graph_version, node_template, node, second_node, node_connection
    ):
        """get_node_connection returns NC data when set in context."""
        serializer = NodeReadSerializer(
            node, context={"node_connection": node_connection}
        )
        nc_data = serializer.data["node_connection"]
        assert nc_data is not None
        assert str(nc_data["source_node_id"]) == str(node.id)
        assert str(nc_data["target_node_id"]) == str(second_node.id)

    def test_node_connection_none_without_context(self, node):
        """get_node_connection returns None when no NC in context."""
        serializer = NodeReadSerializer(node)
        assert serializer.data["node_connection"] is None


@pytest.mark.unit
class TestNodeReadSerializerInputMappings:
    """Tests for NodeReadSerializer.get_input_mappings reconstruction."""

    def test_subgraph_with_edges_returns_mapping(
        self,
        db,
        graph_version,
        subgraph_node,
        dynamic_node_template,
    ):
        """Subgraph node with edges returns reconstructed input_mappings dict."""
        from agent_playground.models.node_connection import NodeConnection

        # Create source node with output port (dynamic template allows custom keys)
        source_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=dynamic_node_template,
            type=NodeType.ATOMIC,
            name="SourceNode",
            config={},
            position={},
        )
        source_port = Port.no_workspace_objects.create(
            node=source_node,
            key="custom",
            display_name="data_out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )

        # Create input ports on subgraph node
        mapped_port = Port.no_workspace_objects.create(
            node=subgraph_node,
            key="custom",
            display_name="context",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        unmapped_port = Port.no_workspace_objects.create(
            node=subgraph_node,
            key="custom",
            display_name="question",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )

        # NodeConnection required before creating edge
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=source_node,
            target_node=subgraph_node,
        )

        # Create edge for mapped port
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=source_port,
            target_port=mapped_port,
        )

        serializer = NodeReadSerializer(subgraph_node)
        data = serializer.data

        assert data["input_mappings"] is not None
        # Convert list to dict for easier testing
        mappings = {m["key"]: m["value"] for m in data["input_mappings"]}
        assert mappings["context"] == "SourceNode.data_out"
        assert mappings["question"] is None

    def test_atomic_node_returns_none(self, node):
        """Atomic node returns None for input_mappings."""
        serializer = NodeReadSerializer(node)
        assert serializer.data["input_mappings"] is None

    def test_subgraph_without_input_ports_returns_none(self, subgraph_node):
        """Subgraph node with no input ports returns None."""
        serializer = NodeReadSerializer(subgraph_node)
        assert serializer.data["input_mappings"] is None


class TestNodeWriteSerializer:
    """Tests for NodeWriteSerializer."""

    # ==================== Valid Atomic Node Tests ====================

    def test_valid_atomic_node_minimal(self, node_template):
        """Test validation of minimal valid atomic node data."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["type"] == NodeType.ATOMIC

    def test_valid_atomic_node_with_config(self, node_template):
        """Test validation of atomic node with config."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "LLM Node",
            "node_template_id": str(node_template.id),
            "config": {
                "model": "gpt-4",
                "temperature": 0.7,
                "messages": [{"role": "user", "content": "Hello"}],
            },
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["config"]["model"] == "gpt-4"

    def test_valid_atomic_node_with_position(self, node_template):
        """Test validation of atomic node with position."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Positioned Node",
            "node_template_id": str(node_template.id),
            "position": {"x": 100, "y": 200},
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["position"]["x"] == 100

    def test_valid_atomic_node_with_ports(self, node_template):
        """Test validation of atomic node with ports."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Node with Ports",
            "node_template_id": str(node_template.id),
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "input1",
                    "display_name": "input1",
                    "direction": PortDirection.INPUT,
                    "data_schema": {"type": "string"},
                },
                {
                    "id": str(uuid.uuid4()),
                    "key": "output1",
                    "display_name": "output1",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                },
            ],
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["ports"]) == 2

    def test_valid_atomic_node_full(self, node_template):
        """Test validation of fully configured atomic node."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Full Node",
            "node_template_id": str(node_template.id),
            "config": {"param1": "value1", "param2": 123},
            "position": {"x": 500, "y": 300, "width": 200, "height": 100},
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "prompt",
                    "display_name": "prompt",
                    "direction": PortDirection.INPUT,
                    "data_schema": {"type": "string"},
                    "required": True,
                    "metadata": {"description": "User input"},
                },
            ],
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    # ==================== Valid Subgraph Node Tests ====================

    def test_valid_subgraph_node_minimal(self, active_referenced_graph_version):
        """Test validation of minimal valid subgraph node data."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["type"] == NodeType.SUBGRAPH

    def test_valid_subgraph_node_with_position(self, active_referenced_graph_version):
        """Test validation of subgraph node with position."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
            "position": {"x": 400, "y": 200},
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valid_subgraph_node_with_ports(self, active_referenced_graph_version):
        """Test validation of subgraph node with ports."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "text",
                    "display_name": "text",
                    "direction": PortDirection.INPUT,
                },
            ],
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    # ==================== Atomic Node Validation Failures ====================

    def test_atomic_node_without_template_fails(self):
        """Test that atomic node without template fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "node_template_id" in serializer.errors

    def test_atomic_node_with_null_template_fails(self):
        """Test that atomic node with null template fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": None,
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "node_template_id" in serializer.errors

    def test_atomic_node_with_ref_graph_version_fails(
        self, node_template, active_referenced_graph_version
    ):
        """Test that atomic node with ref_graph_version fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
            "ref_graph_version_id": str(active_referenced_graph_version.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "ref_graph_version_id" in serializer.errors

    # ==================== Subgraph Node Validation Failures ====================

    def test_subgraph_node_without_ref_graph_version_fails(self):
        """Test that subgraph node without ref_graph_version fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "ref_graph_version_id" in serializer.errors

    def test_subgraph_node_with_null_ref_fails(self):
        """Test that subgraph node with null ref fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": None,
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "ref_graph_version_id" in serializer.errors

    def test_subgraph_node_with_template_fails(
        self, node_template, active_referenced_graph_version
    ):
        """Test that subgraph node with template fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "node_template_id" in serializer.errors

    def test_subgraph_node_with_config_fails(self, active_referenced_graph_version):
        """Test that subgraph node with non-empty config fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
            "config": {"key": "value"},
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "config" in serializer.errors

    def test_subgraph_node_with_empty_config_passes(
        self, active_referenced_graph_version
    ):
        """Test that subgraph node with empty config passes validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
            "config": {},
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    # ==================== Required Field Validation Tests ====================

    def test_missing_id_fails(self, node_template):
        """Test that missing id fails validation."""
        data = {
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "id" in serializer.errors

    def test_missing_type_fails(self, node_template):
        """Test that missing type fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "name": "Test Node",
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "type" in serializer.errors

    def test_missing_name_fails(self, node_template):
        """Test that missing name fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_invalid_type_fails(self, node_template):
        """Test that invalid type fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": "invalid_type",
            "name": "Test Node",
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "type" in serializer.errors

    def test_empty_name_fails(self, node_template):
        """Test that empty name fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "",
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_name_too_long_fails(self, node_template):
        """Test that name exceeding max length fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "a" * 256,  # Max is 255
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    # ==================== Default Values Tests ====================

    def test_defaults_applied_correctly(self, node_template):
        """Test that default values are applied correctly."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["config"] == {}
        assert serializer.validated_data["position"] == {}
        assert serializer.validated_data["ports"] == []

    # ==================== Nested Port Validation Tests ====================

    def test_invalid_port_data_fails(self, node_template):
        """Test that invalid port data fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
            "ports": [
                {
                    # Missing required fields
                    "key": "input1",
                }
            ],
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "ports" in serializer.errors

    def test_multiple_ports_validation(self, node_template):
        """Test validation with multiple valid ports."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Multi-Port Node",
            "node_template_id": str(node_template.id),
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": f"port_{i}",
                    "display_name": f"port_{i}",
                    "direction": PortDirection.INPUT,
                }
                for i in range(10)
            ],
        }
        serializer = NodeWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["ports"]) == 10

    # ==================== UUID Validation Tests ====================

    def test_invalid_node_template_uuid_fails(self):
        """Test that invalid UUID for node_template_id fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": "not-a-uuid",
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "node_template_id" in serializer.errors

    def test_invalid_ref_graph_version_uuid_fails(self):
        """Test that invalid UUID for ref_graph_version_id fails validation."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.SUBGRAPH,
            "name": "Subgraph Node",
            "ref_graph_version_id": "not-a-uuid",
        }
        serializer = NodeWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "ref_graph_version_id" in serializer.errors

    def test_nonexistent_template_uuid_accepted(self):
        """Test that nonexistent but valid UUID is accepted (DB validation happens later)."""
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(uuid.uuid4()),  # Valid UUID but doesn't exist
        }
        serializer = NodeWriteSerializer(data=data)
        # Serializer validates format, not existence
        assert serializer.is_valid(), serializer.errors


@pytest.mark.unit
class TestPromptTemplateDataSerializer:
    """Tests for PromptTemplateDataSerializer validation."""

    def test_valid_minimal(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello"}],
                }
            ]
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["messages"] == data["messages"]
        assert s.validated_data["response_format"] == "text"
        assert s.validated_data["model"] is None
        assert s.validated_data["temperature"] is None

    def test_messages_required(self):
        s = PromptTemplateDataSerializer(data={})
        assert not s.is_valid()
        assert "messages" in s.errors

    def test_messages_must_have_role_and_content(self):
        data = {"messages": [{"id": "msg-0", "role": "user"}]}
        s = PromptTemplateDataSerializer(data=data)
        assert not s.is_valid()
        assert "messages" in s.errors

    def test_messages_cannot_be_empty(self):
        data = {"messages": []}
        s = PromptTemplateDataSerializer(data=data)
        assert not s.is_valid()
        assert "messages" in s.errors

    def test_response_format_choices(self):
        base = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        for fmt in ("text", "json", "json_schema"):
            data = {**base, "response_format": fmt}
            s = PromptTemplateDataSerializer(data=data)
            assert s.is_valid(), s.errors

        data = {**base, "response_format": "xml"}
        s = PromptTemplateDataSerializer(data=data)
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_response_format_rejects_uuid_string(self):
        """Test that response_format rejects UUID strings (not supported for execution)."""
        base = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        # UUID strings are not supported for execution
        uuid_str = str(uuid.uuid4())
        data = {**base, "response_format": uuid_str}
        s = PromptTemplateDataSerializer(data=data)
        assert not s.is_valid()
        assert "response_format" in s.errors

        # UUID with dashes also rejected
        uuid_with_dashes = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        data = {**base, "response_format": uuid_with_dashes}
        s = PromptTemplateDataSerializer(data=data)
        assert not s.is_valid()
        assert "response_format" in s.errors

    def test_response_format_rejects_invalid_uuid_string(self):
        """Test that response_format rejects invalid UUID strings."""
        base = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        # Test with invalid UUID formats
        invalid_values = [
            "not-a-uuid",
            "12345",
            "xml",
            "invalid-uuid-format",
        ]
        for invalid in invalid_values:
            data = {**base, "response_format": invalid}
            s = PromptTemplateDataSerializer(data=data)
            assert not s.is_valid(), f"Should reject {invalid}"
            assert "response_format" in s.errors

    def test_response_format_preserves_id_field(self):
        """Test that response_format preserves id field when provided with schema object."""
        base = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        # Test with id field present
        data = {
            **base,
            "response_format": {
                "id": "test-uuid-12345",
                "name": "ResponseSchema",
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            },
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        vd = s.validated_data
        assert "id" in vd["response_format"], "id field should be preserved"
        assert vd["response_format"]["id"] == "test-uuid-12345"
        assert vd["response_format"]["name"] == "ResponseSchema"
        assert "schema" in vd["response_format"]

        # Test without id field
        data_no_id = {
            **base,
            "response_format": {
                "name": "ResponseSchema",
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                },
            },
        }
        s_no_id = PromptTemplateDataSerializer(data=data_no_id)
        assert s_no_id.is_valid(), s_no_id.errors
        vd_no_id = s_no_id.validated_data
        assert "id" not in vd_no_id["response_format"], (
            "id should not be present when not provided"
        )
        assert vd_no_id["response_format"]["name"] == "ResponseSchema"
        assert "schema" in vd_no_id["response_format"]

    def test_optional_fields_default_to_none(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        vd = s.validated_data
        assert vd["model"] is None
        assert vd["temperature"] is None
        assert vd["max_tokens"] is None
        assert vd["top_p"] is None
        assert vd["frequency_penalty"] is None
        assert vd["presence_penalty"] is None
        assert vd["response_schema"] is None
        assert vd["prompt_template_id"] is None
        assert vd["prompt_version_id"] is None
        assert vd["save_prompt_version"] is False
        assert vd["variable_names"] is None
        assert vd["metadata"] is None
        assert vd["commit_message"] is None

    def test_variable_names_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello {{name}}"}],
                }
            ],
            "variable_names": {"name": ["Alice", "Bob"]},
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["variable_names"] == {"name": ["Alice", "Bob"]}

    def test_metadata_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "metadata": {"source": "playground", "version": 1},
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["metadata"] == {"source": "playground", "version": 1}

    def test_commit_message_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "save_prompt_version": True,
            "commit_message": "Initial version",
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["commit_message"] == "Initial version"

    def test_commit_message_allows_blank(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "commit_message": "",
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["commit_message"] == ""

    def test_output_format_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "output_format": "markdown",
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["output_format"] == "markdown"

    def test_output_format_allows_blank(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "output_format": "",
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["output_format"] == ""

    def test_output_format_allows_null(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "output_format": None,
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["output_format"] is None

    def test_tools_accepted(self):
        tools = [
            {"type": "function", "function": {"name": "get_weather", "parameters": {}}},
        ]
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "tools": tools,
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["tools"] == tools

    def test_tools_allows_null(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "tools": None,
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["tools"] is None

    def test_tools_rejects_non_list(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "tools": "not a list",
        }
        s = PromptTemplateDataSerializer(data=data)
        assert not s.is_valid()
        assert "tools" in s.errors

    def test_tool_choice_string_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "tool_choice": "auto",
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["tool_choice"] == "auto"

    def test_tool_choice_object_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["tool_choice"]["type"] == "function"

    def test_tool_choice_allows_null(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "tool_choice": None,
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["tool_choice"] is None

    def test_model_detail_accepted(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "model_detail": {"provider": "openai", "version": "2024-01"},
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["model_detail"]["provider"] == "openai"

    def test_model_detail_allows_null(self):
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "model_detail": None,
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["model_detail"] is None

    def test_all_new_fields_together(self):
        """Test sending all new fields in a single payload."""
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello {{name}}"}],
                }
            ],
            "model": "gpt-4o",
            "temperature": 0.7,
            "output_format": "json",
            "tools": [{"type": "function", "function": {"name": "search"}}],
            "tool_choice": "auto",
            "model_detail": {"provider": "openai"},
            "variable_names": {"name": []},
            "metadata": {"source": "playground"},
            "commit_message": "Initial",
            "save_prompt_version": True,
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        vd = s.validated_data
        assert vd["output_format"] == "json"
        assert len(vd["tools"]) == 1
        assert vd["tool_choice"] == "auto"
        assert vd["model_detail"]["provider"] == "openai"
        assert vd["variable_names"] == {"name": []}
        assert vd["metadata"]["source"] == "playground"
        assert vd["commit_message"] == "Initial"
        assert vd["save_prompt_version"] is True

    def test_new_optional_fields_default_to_none(self):
        """All new optional fields default to None when omitted."""
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        s = PromptTemplateDataSerializer(data=data)
        assert s.is_valid(), s.errors
        vd = s.validated_data
        assert vd["output_format"] is None
        assert vd["tools"] is None
        assert vd["tool_choice"] is None
        assert vd["model_detail"] is None


@pytest.mark.unit
class TestCreateNodeSerializer:
    """Tests for CreateNodeSerializer validation."""

    def test_valid_atomic_minimal(self):
        data = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "My Node",
            "node_template_id": str(uuid.uuid4()),
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors

    def test_valid_subgraph_minimal(self):
        data = {
            "id": str(uuid.uuid4()),
            "type": "subgraph",
            "name": "Sub Node",
            "ref_graph_version_id": str(uuid.uuid4()),
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors

    def test_atomic_missing_template_id(self):
        data = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
        }
        s = CreateNodeSerializer(data=data)
        assert not s.is_valid()
        assert "node_template_id" in s.errors

    def test_subgraph_without_ref_version_id_is_valid(self):
        """Subgraph nodes can be created without ref_graph_version_id (set later via PATCH)."""
        data = {
            "id": str(uuid.uuid4()),
            "type": "subgraph",
            "name": "Node",
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors

    def test_id_required(self):
        data = {
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(uuid.uuid4()),
        }
        s = CreateNodeSerializer(data=data)
        assert not s.is_valid()
        assert "id" in s.errors

    def test_with_ports(self):
        port_id = str(uuid.uuid4())
        data = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(uuid.uuid4()),
            "ports": [
                {
                    "id": port_id,
                    "key": "custom",
                    "display_name": "input_text",
                    "direction": "input",
                }
            ],
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert len(s.validated_data["ports"]) == 1

    def test_with_prompt_template(self):
        data = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(uuid.uuid4()),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Hello {{name}}"}],
                    }
                ],
            },
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["prompt_template"]["messages"] is not None

    def test_with_prompt_template_and_new_fields(self):
        data = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "LLM Node",
            "node_template_id": str(uuid.uuid4()),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{input}}"}],
                    }
                ],
                "model": "gpt-4o",
                "temperature": 0.7,
                "variable_names": {"input": ["sample"]},
                "metadata": {"source": "playground"},
                "tools": [{"type": "function", "function": {"name": "search"}}],
                "tool_choice": "auto",
            },
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "response",
                    "display_name": "llm_output",
                    "direction": "output",
                    "data_schema": {"type": "string"},
                }
            ],
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        pt = s.validated_data["prompt_template"]
        assert pt["variable_names"] == {"input": ["sample"]}
        assert pt["tools"][0]["function"]["name"] == "search"
        assert len(s.validated_data["ports"]) == 1

    def test_subgraph_with_ref_and_ports(self):
        data = {
            "id": str(uuid.uuid4()),
            "type": "subgraph",
            "name": "Sub Node",
            "ref_graph_version_id": str(uuid.uuid4()),
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "custom",
                    "display_name": "summary_output",
                    "direction": "output",
                    "ref_port_id": str(uuid.uuid4()),
                }
            ],
        }
        s = CreateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert len(s.validated_data["ports"]) == 1


@pytest.mark.unit
class TestUpdateNodeSerializer:
    """Tests for UpdateNodeSerializer validation."""

    def test_empty_is_valid(self):
        s = UpdateNodeSerializer(data={})
        assert s.is_valid(), s.errors

    def test_partial_name_only(self):
        s = UpdateNodeSerializer(data={"name": "New Name"})
        assert s.is_valid(), s.errors
        assert s.validated_data["name"] == "New Name"

    def test_partial_position_only(self):
        s = UpdateNodeSerializer(data={"position": {"x": 50, "y": 60}})
        assert s.is_valid(), s.errors
        assert s.validated_data["position"] == {"x": 50, "y": 60}

    def test_name_with_reserved_chars_fails(self):
        s = UpdateNodeSerializer(data={"name": "Node.One"})
        assert not s.is_valid()
        assert "name" in s.errors

    def test_ref_graph_version_id_accepted(self):
        data = {"ref_graph_version_id": str(uuid.uuid4())}
        s = UpdateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["ref_graph_version_id"] is not None

    def test_ref_graph_version_id_allows_null(self):
        data = {"ref_graph_version_id": None}
        s = UpdateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["ref_graph_version_id"] is None

    def test_ref_graph_version_id_invalid_uuid_fails(self):
        data = {"ref_graph_version_id": "not-a-uuid"}
        s = UpdateNodeSerializer(data=data)
        assert not s.is_valid()
        assert "ref_graph_version_id" in s.errors

    def test_ports_field_accepted(self):
        """ports field is now accepted by UpdateNodeSerializer for replacing output ports."""
        data = {
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "custom",
                    "display_name": "my_port",
                    "direction": "output",
                }
            ]
        }
        s = UpdateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert "ports" in s.validated_data
        assert len(s.validated_data["ports"]) == 1
        assert s.validated_data["ports"][0]["display_name"] == "my_port"

    def test_prompt_template_with_all_new_fields(self):
        data = {
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{input}}"}],
                    }
                ],
                "model": "gpt-4o",
                "variable_names": {"input": []},
                "metadata": {"source": "test"},
                "commit_message": "test commit",
                "save_prompt_version": True,
            }
        }
        s = UpdateNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        pt = s.validated_data["prompt_template"]
        assert pt["variable_names"] == {"input": []}
        assert pt["metadata"]["source"] == "test"
        assert pt["commit_message"] == "test commit"


@pytest.mark.unit
class TestNodeNameReservedCharValidation:
    """Tests for reserved character validation in node name fields."""

    @pytest.mark.parametrize("char", [".", "[", "]", "{", "}"])
    def test_write_serializer_rejects_reserved_chars(self, node_template, char):
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": f"Bad{char}Name",
            "node_template_id": str(node_template.id),
        }
        s = NodeWriteSerializer(data=data)
        assert not s.is_valid()
        assert "name" in s.errors

    @pytest.mark.parametrize("char", [".", "[", "]", "{", "}"])
    def test_create_serializer_rejects_reserved_chars(self, char):
        data = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": f"Bad{char}Name",
            "node_template_id": str(uuid.uuid4()),
        }
        s = CreateNodeSerializer(data=data)
        assert not s.is_valid()
        assert "name" in s.errors

    @pytest.mark.parametrize("char", [".", "[", "]", "{", "}"])
    def test_update_serializer_rejects_reserved_chars(self, char):
        s = UpdateNodeSerializer(data={"name": f"Bad{char}Name"})
        assert not s.is_valid()
        assert "name" in s.errors

    def test_write_serializer_accepts_clean_name(self, node_template):
        data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Good_Name-123",
            "node_template_id": str(node_template.id),
        }
        s = NodeWriteSerializer(data=data)
        assert s.is_valid(), s.errors

"""Tests for agent_playground.services.node_crud — business-logic layer."""

import uuid
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from agent_playground.models.choices import NodeType, PortDirection, PortMode
from agent_playground.models.edge import Edge
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.serializers.node import CreateNodeSerializer
from agent_playground.services.node_crud import (
    _build_prompt_config_snapshot,
    _create_default_output_port_from_prompt,
    _create_input_ports_from_prompt,
    _create_subgraph_input_ports,
    _create_subgraph_output_ports,
    _dedupe_name,
    _extract_variables,
    _get_next_template_version,
    _output_data_schema,
    _replace_output_ports,
    _validate_subgraph_fe_ports,
    cascade_soft_delete_node,
    cascade_soft_delete_node_connection,
    create_node,
    update_node,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion

# ── Helpers ────────────────────────────────────────────────────────────


def _base_prompt_data(**overrides):
    data = {
        "messages": [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "Hello {{context}}"}],
            }
        ],
    }
    data.update(overrides)
    return data


def _make_ref_port(graph_version, direction, display_name="port"):
    """Create a real Port in a graph version so ref_port_id FK is satisfied."""
    ref_node = Node(
        graph_version=graph_version,
        type=NodeType.ATOMIC,
        name=f"ref_{display_name}",
        config={},
        position={},
    )
    ref_node.save(skip_validation=True)
    port = Port(
        node=ref_node,
        key="custom",
        display_name=display_name,
        direction=direction,
        data_schema={"type": "string"},
    )
    port.save(skip_validation=True)
    return port


# ── create_node ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCreateNode:
    def test_create_atomic_node_with_fe_id(
        self, db, graph_version, node_template, user, organization, workspace
    ):
        fe_id = uuid.uuid4()
        data = {
            "id": fe_id,
            "type": NodeType.ATOMIC,
            "name": "My Node",
            "node_template_id": node_template.id,
        }
        node, nc = create_node(graph_version, data, user, organization, workspace)

        assert node.id == fe_id
        assert node.type == NodeType.ATOMIC
        assert node.node_template == node_template
        assert nc is None

    def test_create_atomic_node_validates_template_config(
        self, db, graph_version, user, organization, workspace
    ):
        template = NodeTemplate.no_workspace_objects.create(
            name="strict_code_config",
            display_name="Strict Code Config",
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["language", "code"],
                "properties": {
                    "language": {"type": "string", "enum": ["python"]},
                    "code": {"type": "string", "minLength": 1},
                },
            },
        )

        with pytest.raises(ValidationError, match="Invalid config"):
            create_node(
                graph_version,
                {
                    "id": uuid.uuid4(),
                    "type": NodeType.ATOMIC,
                    "name": "Code Node",
                    "node_template_id": template.id,
                    "config": {"language": "ruby", "code": "puts 1"},
                },
                user,
                organization,
                workspace,
            )

    def test_create_atomic_node_persists_template_config(
        self, db, graph_version, user, organization, workspace
    ):
        template = NodeTemplate.no_workspace_objects.create(
            name="code_execution",
            display_name="Code Execution",
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["language", "code"],
                "properties": {
                    "language": {"type": "string", "enum": ["python"]},
                    "code": {"type": "string", "minLength": 1},
                    "timeout_ms": {"type": "integer"},
                },
            },
        )
        config = {
            "language": "python",
            "code": "result = inputs",
            "timeout_ms": 1000,
        }

        node, _ = create_node(
            graph_version,
            {
                "id": uuid.uuid4(),
                "type": NodeType.ATOMIC,
                "name": "Code Node",
                "node_template_id": template.id,
                "config": config,
            },
            user,
            organization,
            workspace,
        )

        assert node.config == config

    def test_create_node_serializer_preserves_atomic_config(self):
        config = {"language": "python", "code": "result = inputs"}
        serializer = CreateNodeSerializer(
            data={
                "id": uuid.uuid4(),
                "type": NodeType.ATOMIC,
                "name": "Code Node",
                "node_template_id": uuid.uuid4(),
                "config": config,
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["config"] == config

    def test_create_llm_node_auto_creates_pt_pv(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Node",
            "node_template_id": llm_node_template.id,
            "prompt_template": _base_prompt_data(),
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        assert ptn.prompt_template is not None
        assert ptn.prompt_version is not None
        assert ptn.prompt_version.is_draft is True

    def test_create_llm_node_with_existing_pt(
        self,
        db,
        graph_version,
        llm_node_template,
        prompt_template,
        user,
        organization,
        workspace,
    ):
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Node",
            "node_template_id": llm_node_template.id,
            "prompt_template": _base_prompt_data(
                prompt_template_id=prompt_template.id,
            ),
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        assert ptn.prompt_template_id == prompt_template.id
        assert ptn.prompt_version.is_draft is True

    def test_create_llm_node_with_existing_draft_pv(
        self,
        db,
        graph_version,
        llm_node_template,
        prompt_template,
        draft_prompt_version,
        user,
        organization,
        workspace,
    ):
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Node",
            "node_template_id": llm_node_template.id,
            "prompt_template": _base_prompt_data(
                prompt_template_id=prompt_template.id,
                prompt_version_id=draft_prompt_version.id,
            ),
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        # Should reuse the same draft PV (updated in-place)
        assert ptn.prompt_version_id == draft_prompt_version.id
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.is_draft is True

    def test_create_llm_node_with_committed_pv(
        self,
        db,
        graph_version,
        llm_node_template,
        prompt_template,
        prompt_version,
        user,
        organization,
        workspace,
    ):
        # prompt_version fixture is committed (is_draft defaults False)
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Node",
            "node_template_id": llm_node_template.id,
            "prompt_template": _base_prompt_data(
                prompt_template_id=prompt_template.id,
                prompt_version_id=prompt_version.id,
            ),
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        # Should create a new draft PV (not reuse the committed one)
        assert ptn.prompt_version_id != prompt_version.id
        assert ptn.prompt_version.is_draft is True

    def test_create_llm_node_creates_ports_from_variables(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Node",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Answer {{question}} given {{context}}",
                            }
                        ],
                    }
                ],
            },
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        output_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT
        )
        assert input_ports.count() == 2
        names = set(input_ports.values_list("display_name", flat=True))
        assert names == {"question", "context"}
        assert output_ports.count() == 1
        assert output_ports.first().key == "response"

    def test_create_node_with_source_node_creates_nc(
        self, db, graph_version, node_template, node, user, organization, workspace
    ):
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Node B",
            "node_template_id": node_template.id,
            "source_node_id": node.id,
        }
        new_node, nc = create_node(graph_version, data, user, organization, workspace)

        assert nc is not None
        assert nc.source_node_id == node.id
        assert nc.target_node_id == new_node.id

    def test_create_node_without_source_node_no_nc(
        self, db, graph_version, node_template, user, organization, workspace
    ):
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Standalone",
            "node_template_id": node_template.id,
        }
        _, nc = create_node(graph_version, data, user, organization, workspace)
        assert nc is None

    def test_create_node_with_fe_ports(
        self, db, graph_version, node_template, user, organization, workspace
    ):
        port_id = uuid.uuid4()
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Node with ports",
            "node_template_id": node_template.id,
            "ports": [
                {
                    "id": port_id,
                    "key": "custom",
                    "display_name": "my_input",
                    "direction": PortDirection.INPUT,
                },
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)
        port = Port.no_workspace_objects.get(id=port_id)
        assert port.node_id == node.id
        assert port.display_name == "my_input"

    def test_create_subgraph_node_auto_creates_ports(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        exposed = [
            {
                "display_name": "inp",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            data = {
                "id": uuid.uuid4(),
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": active_referenced_graph_version.id,
            }
            node, _ = create_node(graph_version, data, user, organization, workspace)

        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 2
        directions = set(ports.values_list("direction", flat=True))
        assert directions == {PortDirection.INPUT, PortDirection.OUTPUT}

    def test_create_subgraph_node_with_input_mappings(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        node_template,
        user,
        organization,
        workspace,
    ):
        """Create subgraph node with explicit input_mappings creates ports and edges."""
        # Create a source node with output port
        source_node = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="DataLoader",
            node_template=node_template,
            config={},
            position={},
        )
        source_node.save(skip_validation=True)
        source_port = Port(
            node=source_node,
            key="custom",
            display_name="output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        source_port.save(skip_validation=True)

        # Create subgraph node with input_mappings
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Pipeline",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "source_node_id": source_node.id,  # Create NodeConnection
            "input_mappings": [
                {"key": "context", "value": "DataLoader.output"},
                {"key": "question", "value": None},  # Globally exposed, no edge
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        # Verify input ports created from mappings keys only
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 2
        port_names = set(input_ports.values_list("display_name", flat=True))
        assert port_names == {"context", "question"}

        # Verify edge created for non-null mapping
        edges = Edge.no_workspace_objects.filter(target_port__node=node)
        assert edges.count() == 1
        edge = edges.first()
        assert edge.source_port == source_port
        assert edge.target_port.display_name == "context"

    def test_create_subgraph_node_without_input_mappings_backwards_compatible(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """Create subgraph node without input_mappings uses auto-creation (backwards compat)."""
        exposed = [
            {
                "display_name": "inp",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            data = {
                "id": uuid.uuid4(),
                "type": NodeType.SUBGRAPH,
                "name": "Sub",
                "ref_graph_version_id": active_referenced_graph_version.id,
                # No input_mappings provided
            }
            node, _ = create_node(graph_version, data, user, organization, workspace)

        # Verify ports auto-created from exposed ports
        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 2
        directions = set(ports.values_list("direction", flat=True))
        assert directions == {PortDirection.INPUT, PortDirection.OUTPUT}

    def test_input_mappings_with_nonexistent_source(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """Invalid source reference creates port but no edge (graceful handling)."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Pipeline",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "input_mappings": [
                {"key": "context", "value": "NonExistentNode.output"},  # Invalid source
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        # Verify port created
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 1
        assert input_ports.first().display_name == "context"

        # Verify no edge created (graceful handling)
        edges = Edge.no_workspace_objects.filter(target_port__node=node)
        assert edges.count() == 0

    def test_input_mappings_null_value(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """Null mapping value creates globally exposed input (no edge)."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Pipeline",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "input_mappings": [
                {"key": "question", "value": None},  # Globally exposed
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        # Verify port created
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 1
        assert input_ports.first().display_name == "question"

        # Verify no edge created
        edges = Edge.no_workspace_objects.filter(target_port__node=node)
        assert edges.count() == 0

    def test_input_mappings_empty_dict(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """Empty input_mappings dict creates no input ports (valid for output-only subgraphs)."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Pipeline",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "input_mappings": [],  # Empty list
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        # Verify NO input ports created
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 0

    def test_input_mappings_skips_edge_without_node_connection(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """input_mappings skips edge creation when no NodeConnection exists."""
        # Create a source node with output port but NO NodeConnection
        source_node = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Loader",
            config={},
            position={},
        )
        source_node.save(skip_validation=True)
        Port(
            node=source_node,
            key="custom",
            display_name="data_out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)

        # Create subgraph node — no source_node_id, so no NC pre-created
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "SubPipeline",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "input_mappings": [
                {"key": "ctx", "value": "Loader.data_out"},
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        # No NodeConnection should be auto-created
        nc = NodeConnection.no_workspace_objects.filter(
            graph_version=graph_version,
            source_node=source_node,
            target_node=node,
        )
        assert (
            not nc.exists()
        ), "NodeConnection should NOT be auto-created from input_mappings"

        # No edge should be created without a NodeConnection
        edges = Edge.no_workspace_objects.filter(target_port__node=node)
        assert edges.count() == 0

    def test_input_mappings_creates_edge_when_nc_exists_via_source_node_id(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        node_template,
        user,
        organization,
        workspace,
    ):
        """input_mappings creates edge when NC already exists via source_node_id."""
        # Create source node with output port
        source_node = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="DataLoader",
            node_template=node_template,
            config={},
            position={},
        )
        source_node.save(skip_validation=True)
        Port(
            node=source_node,
            key="custom",
            display_name="output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)

        # Create subgraph with source_node_id (creates NC) AND input_mappings
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Pipeline",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "source_node_id": source_node.id,
            "input_mappings": [
                {"key": "context", "value": "DataLoader.output"},
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        # Should have exactly 1 NodeConnection from source_node_id
        nc_count = NodeConnection.no_workspace_objects.filter(
            graph_version=graph_version,
            source_node=source_node,
            target_node=node,
        ).count()
        assert nc_count == 1

        # Edge should be created since NC exists
        edges = Edge.no_workspace_objects.filter(target_port__node=node)
        assert edges.count() == 1

    def test_input_mappings_edge_creation_failure_handled_gracefully(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """Edge.save() failure is caught and logged, not raised."""
        source_node = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Src",
            config={},
            position={},
        )
        source_node.save(skip_validation=True)
        Port(
            node=source_node,
            key="custom",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)

        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Sub",
            "ref_graph_version_id": active_referenced_graph_version.id,
            "source_node_id": source_node.id,
            "input_mappings": [
                {"key": "inp", "value": "Src.out"},
            ],
        }

        # Patch Edge.save to raise — should not crash create_node
        with patch.object(Edge, "save", side_effect=ValidationError("boom")):
            node, _ = create_node(graph_version, data, user, organization, workspace)

        # Node and port should still be created even though edge failed
        assert node is not None
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 1
        assert input_ports.first().display_name == "inp"

        # No edge (save was mocked to fail)
        edges = Edge.no_workspace_objects.filter(target_port__node=node)
        assert edges.count() == 0


# ── update_node ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestUpdateNode:
    def test_update_name_only(self, db, node, user, organization, workspace):
        updated = update_node(node, {"name": "Renamed"}, user, organization, workspace)
        assert updated.name == "Renamed"

    def test_update_position_only(self, db, node, user, organization, workspace):
        updated = update_node(
            node, {"position": {"x": 42, "y": 99}}, user, organization, workspace
        )
        assert updated.position == {"x": 42, "y": 99}

    def test_update_atomic_node_validates_template_config(
        self, db, graph_version, user, organization, workspace
    ):
        template = NodeTemplate.no_workspace_objects.create(
            name="strict_update_config",
            display_name="Strict Update Config",
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["language", "code"],
                "properties": {
                    "language": {"type": "string", "enum": ["python"]},
                    "code": {"type": "string", "minLength": 1},
                },
            },
        )
        node, _ = create_node(
            graph_version,
            {
                "id": uuid.uuid4(),
                "type": NodeType.ATOMIC,
                "name": "Code Node",
                "node_template_id": template.id,
                "config": {"language": "python", "code": "result = 1"},
            },
            user,
            organization,
            workspace,
        )

        with pytest.raises(ValidationError, match="Invalid config"):
            update_node(
                node,
                {"config": {"language": "javascript", "code": "result = 1"}},
                user,
                organization,
                workspace,
            )

    def test_update_prompt_template_updates_pv(
        self,
        db,
        node,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        # Re-point the PTN to the draft version so we can test in-place update
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        new_messages = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "Updated {{var}}"}],
            }
        ]
        update_node(
            node,
            {
                "prompt_template": {
                    "messages": new_messages,
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                }
            },
            user,
            organization,
            workspace,
        )
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.prompt_config_snapshot["messages"] == new_messages

    def test_update_prompt_reconciles_ports_add_new(
        self,
        db,
        node,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "{{existing}} and {{new_var}}"}
                            ],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                }
            },
            user,
            organization,
            workspace,
        )
        input_names = set(
            Port.no_workspace_objects.filter(
                node=node, direction=PortDirection.INPUT
            ).values_list("display_name", flat=True)
        )
        assert "existing" in input_names
        assert "new_var" in input_names

    def test_update_prompt_reconciles_ports_remove_old(
        self,
        db,
        node,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        # Create an existing port that will be removed
        old_port = Port(
            node=node,
            key="custom",
            display_name="old_var",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        old_port.save(skip_validation=True)

        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{new_only}}"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                }
            },
            user,
            organization,
            workspace,
        )
        old_port.refresh_from_db()
        assert old_port.deleted is True

    def test_save_prompt_version_true_commits_draft(
        self,
        db,
        node,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "commit me"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                    "save_prompt_version": True,
                }
            },
            user,
            organization,
            workspace,
        )
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.is_draft is False

    def test_save_prompt_version_true_committed_creates_new(
        self,
        db,
        node,
        prompt_template,
        prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        # prompt_version is committed (is_draft=False by default)
        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "new committed"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": prompt_version.id,
                    "save_prompt_version": True,
                }
            },
            user,
            organization,
            workspace,
        )
        prompt_template_node.refresh_from_db()
        new_pv = prompt_template_node.prompt_version
        assert new_pv.id != prompt_version.id
        assert new_pv.is_draft is False

    def test_save_prompt_version_false_committed_creates_draft(
        self,
        db,
        node,
        prompt_template,
        prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "draft from committed"}
                            ],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": prompt_version.id,
                    "save_prompt_version": False,
                }
            },
            user,
            organization,
            workspace,
        )
        prompt_template_node.refresh_from_db()
        new_pv = prompt_template_node.prompt_version
        assert new_pv.id != prompt_version.id
        assert new_pv.is_draft is True

    def test_update_subgraph_node_input_mappings(
        self,
        db,
        subgraph_node,
        graph_version,
        node_template,
        user,
        organization,
        workspace,
    ):
        """Update input_mappings replaces existing ports/edges (REPLACE strategy)."""
        # Create initial input ports and edges
        old_source = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="OldSource",
            node_template=node_template,
            config={},
            position={},
        )
        old_source.save(skip_validation=True)
        old_port = Port(
            node=old_source,
            key="custom",
            display_name="old_output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        old_port.save(skip_validation=True)

        # Add initial input port
        initial_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="old_input",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        initial_port.save(skip_validation=True)

        # Create NodeConnection
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=old_source,
            target_node=subgraph_node,
        )

        # Create edge
        initial_edge = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=old_port,
            target_port=initial_port,
        )

        # Create new source node
        new_source = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="NewSource",
            node_template=node_template,
            config={},
            position={},
        )
        new_source.save(skip_validation=True)
        new_port = Port(
            node=new_source,
            key="custom",
            display_name="data",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        new_port.save(skip_validation=True)

        # Create NodeConnection for new source
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=new_source,
            target_node=subgraph_node,
        )

        # Update with new input_mappings
        update_node(
            subgraph_node,
            {
                "input_mappings": [
                    {"key": "context", "value": "NewSource.data"},
                ]
            },
            user,
            organization,
            workspace,
        )

        # Verify old input port deleted
        initial_port.refresh_from_db()
        assert initial_port.deleted is True

        # Verify old edge deleted
        initial_edge.refresh_from_db()
        assert initial_edge.deleted is True

        # Verify new input port created
        new_input_ports = Port.no_workspace_objects.filter(
            node=subgraph_node, direction=PortDirection.INPUT
        )
        assert new_input_ports.count() == 1
        assert new_input_ports.first().display_name == "context"

        # Verify new edge created
        new_edges = Edge.no_workspace_objects.filter(target_port__node=subgraph_node)
        assert new_edges.count() == 1
        assert new_edges.first().source_port == new_port

    def test_update_node_without_input_mappings_key_keeps_ports(
        self,
        db,
        subgraph_node,
        user,
        organization,
        workspace,
    ):
        """Update without input_mappings key keeps existing ports unchanged."""
        # Add initial input port
        initial_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="existing_input",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        initial_port.save(skip_validation=True)

        # Update without input_mappings
        update_node(
            subgraph_node,
            {"name": "Renamed Subgraph"},
            user,
            organization,
            workspace,
        )

        # Verify port still exists
        initial_port.refresh_from_db()
        assert initial_port.deleted is False

        # Verify port count unchanged
        input_ports = Port.no_workspace_objects.filter(
            node=subgraph_node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 1


# ── cascade_soft_delete_node ───────────────────────────────────────────


@pytest.mark.unit
class TestCascadeSoftDeleteNode:
    def test_deletes_edges_on_ports(
        self, db, node, output_port, second_node_input_port, edge
    ):
        cascade_soft_delete_node(node)
        edge.refresh_from_db()
        assert edge.deleted is True

    def test_deletes_node_connections(self, db, node, second_node, node_connection):
        cascade_soft_delete_node(node)
        node_connection.refresh_from_db()
        assert node_connection.deleted is True

    def test_deletes_ports(self, db, node, input_port, output_port):
        cascade_soft_delete_node(node)
        input_port.refresh_from_db()
        output_port.refresh_from_db()
        assert input_port.deleted is True
        assert output_port.deleted is True

    def test_deletes_prompt_template_node(self, db, node, prompt_template_node):
        cascade_soft_delete_node(node)
        prompt_template_node.refresh_from_db()
        assert prompt_template_node.deleted is True

    def test_deletes_node_itself(self, db, node):
        cascade_soft_delete_node(node)
        node.refresh_from_db()
        assert node.deleted is True

    def test_deletes_all_incoming_and_outgoing_edges(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Deleting a middle node deletes both incoming and outgoing edges."""
        # Create a chain: node_1 → node_2 → node_3
        node_1_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_1",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Start"}],
                    }
                ]
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        node_1, _ = create_node(
            graph_version, node_1_data, user, organization, workspace
        )

        node_2_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_2",
            "node_template_id": llm_node_template.id,
            "source_node_id": node_1.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Process {{node_1.output}}"}
                        ],
                    }
                ]
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "result",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        node_2, _ = create_node(
            graph_version, node_2_data, user, organization, workspace
        )

        node_3_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_3",
            "node_template_id": llm_node_template.id,
            "source_node_id": node_2.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Finalize {{node_2.result}}"}
                        ],
                    }
                ]
            },
        }
        node_3, _ = create_node(
            graph_version, node_3_data, user, organization, workspace
        )

        # Capture edges before deletion
        edge_1_to_2 = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port__node=node_1,
            target_port__node=node_2,
        ).first()
        edge_2_to_3 = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port__node=node_2,
            target_port__node=node_3,
        ).first()

        # Verify edges exist before deletion
        assert edge_1_to_2 is not None
        assert edge_2_to_3 is not None
        edges_before = Edge.no_workspace_objects.filter(
            graph_version=graph_version, deleted=False
        )
        assert edges_before.count() == 2  # node_1→node_2 and node_2→node_3

        # Delete middle node (node_2)
        cascade_soft_delete_node(node_2)

        # Verify ALL edges involving node_2 are deleted
        edges_after = Edge.no_workspace_objects.filter(
            graph_version=graph_version, deleted=False
        )
        assert edges_after.count() == 0

        # Refresh and verify the specific edges are deleted
        edge_1_to_2.refresh_from_db()
        edge_2_to_3.refresh_from_db()
        assert edge_1_to_2.deleted is True
        assert edge_2_to_3.deleted is True

    def test_deletes_all_node_connections_as_source_and_target(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Deleting a node deletes all NodeConnections where it's source OR target."""
        # Create: node_1 → node_2 ← node_3 (node_2 is target from both)
        node_1_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_1",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ]
            },
        }
        node_1, _ = create_node(
            graph_version, node_1_data, user, organization, workspace
        )

        node_2_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_2",
            "node_template_id": llm_node_template.id,
            "source_node_id": node_1.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ]
            },
        }
        node_2, nc1 = create_node(
            graph_version, node_2_data, user, organization, workspace
        )

        node_3_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_3",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ]
            },
        }
        node_3, _ = create_node(
            graph_version, node_3_data, user, organization, workspace
        )

        # Create second connection: node_3 → node_2
        nc2 = NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node_3, target_node=node_2
        )

        # Also create: node_2 → node_3 (node_2 as source)
        nc3 = NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node_2, target_node=node_3
        )

        # Verify 3 connections exist
        ncs_before = NodeConnection.no_workspace_objects.filter(
            graph_version=graph_version, deleted=False
        )
        assert ncs_before.count() == 3

        # Delete node_2
        cascade_soft_delete_node(node_2)

        # Verify all 3 connections are deleted (node_2 as source and target)
        nc1.refresh_from_db()
        nc2.refresh_from_db()
        nc3.refresh_from_db()
        assert nc1.deleted is True  # node_1 → node_2
        assert nc2.deleted is True  # node_3 → node_2
        assert nc3.deleted is True  # node_2 → node_3

    def test_cascade_delete_does_not_affect_unrelated_nodes(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Deleting one node doesn't affect unrelated nodes, ports, edges."""
        # Create two separate chains: node_1 → node_2 and node_3 → node_4
        node_1_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_1",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ]
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "out",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        node_1, _ = create_node(
            graph_version, node_1_data, user, organization, workspace
        )

        node_2_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_2",
            "node_template_id": llm_node_template.id,
            "source_node_id": node_1.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Use {{node_1.out}}"}],
                    }
                ]
            },
        }
        node_2, _ = create_node(
            graph_version, node_2_data, user, organization, workspace
        )

        # Second chain (unrelated)
        node_3_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_3",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ]
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "data",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        node_3, _ = create_node(
            graph_version, node_3_data, user, organization, workspace
        )

        node_4_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_4",
            "node_template_id": llm_node_template.id,
            "source_node_id": node_3.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Use {{node_3.data}}"}],
                    }
                ]
            },
        }
        node_4, _ = create_node(
            graph_version, node_4_data, user, organization, workspace
        )

        # Capture edges before deletion
        edge_1_to_2 = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port__node=node_1,
            target_port__node=node_2,
        ).first()
        edge_3_to_4 = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port__node=node_3,
            target_port__node=node_4,
        ).first()

        # Verify 2 edges exist
        assert edge_1_to_2 is not None
        assert edge_3_to_4 is not None
        edges_before = Edge.no_workspace_objects.filter(
            graph_version=graph_version, deleted=False
        )
        assert edges_before.count() == 2

        # Delete node_1 (first chain)
        cascade_soft_delete_node(node_1)

        # Verify node_3 and node_4 (second chain) are unaffected
        node_3.refresh_from_db()
        node_4.refresh_from_db()
        assert node_3.deleted is False
        assert node_4.deleted is False

        # Verify edge from node_3 → node_4 still exists
        edge_3_to_4.refresh_from_db()
        assert edge_3_to_4.deleted is False

        # Verify only edge from node_1 → node_2 is deleted
        edge_1_to_2.refresh_from_db()
        assert edge_1_to_2.deleted is True

    def test_cascade_delete_with_auto_created_edges(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Auto-created edges are properly deleted when node is deleted."""
        # Create source node
        source_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "source",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ]
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_data, user, organization, workspace
        )

        # Create target node with auto-created edge
        target_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "target",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Use {{source.output}}"}],
                    }
                ]
            },
        }
        target_node, _ = create_node(
            graph_version, target_data, user, organization, workspace
        )

        # Capture edge and NodeConnection before deletion
        edge = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port__node=source_node,
            target_port__node=target_node,
        ).first()
        nc = NodeConnection.no_workspace_objects.filter(
            graph_version=graph_version,
            source_node=source_node,
            target_node=target_node,
        ).first()

        # Verify edge and NodeConnection were auto-created
        assert edge is not None
        assert nc is not None

        # Delete source node
        cascade_soft_delete_node(source_node)

        # Verify edge is deleted
        edge.refresh_from_db()
        assert edge.deleted is True

        # Verify NodeConnection is deleted
        nc.refresh_from_db()
        assert nc.deleted is True


# ── cascade_soft_delete_node_connection ────────────────────────────────


@pytest.mark.unit
class TestCascadeSoftDeleteNodeConnection:
    def test_deletes_edges_between_nodes(
        self,
        db,
        node,
        second_node,
        output_port,
        second_node_input_port,
        edge,
        node_connection,
    ):
        cascade_soft_delete_node_connection(node_connection)
        edge.refresh_from_db()
        assert edge.deleted is True

    def test_deletes_nc_itself(self, db, node_connection):
        cascade_soft_delete_node_connection(node_connection)
        node_connection.refresh_from_db()
        assert node_connection.deleted is True

    def test_does_not_delete_unrelated_edges(
        self,
        db,
        graph_version,
        node,
        second_node,
        third_node,
        output_port,
        second_node_input_port,
        third_node_input_port,
        edge,
        node_connection,
    ):
        # Create a NodeConnection + edge from node → third_node (unrelated to node_connection)
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node,
            target_node=third_node,
        )
        unrelated_edge = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=output_port,
            target_port=third_node_input_port,
        )

        cascade_soft_delete_node_connection(node_connection)

        unrelated_edge.refresh_from_db()
        assert unrelated_edge.deleted is False


# ── _extract_variables ─────────────────────────────────────────────────


@pytest.mark.unit
class TestExtractVariables:
    def test_extracts_unique_ordered(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "{{a}} {{b}} {{a}}"}],
            }
        ]
        assert _extract_variables(msgs) == ["a", "b"]

    def test_no_variables(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "Hello world"}],
            }
        ]
        assert _extract_variables(msgs) == []

    def test_multiple_messages(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "{{x}}"}],
            },
            {
                "id": "msg-1",
                "role": "user",
                "content": [{"type": "text", "text": "{{y}} and {{x}}"}],
            },
        ]
        assert _extract_variables(msgs) == ["x", "y"]

    def test_dot_notation_variable(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "Use {{Node1.response}} here"}],
            }
        ]
        assert _extract_variables(msgs) == ["Node1.response"]

    def test_deep_dot_notation_variable(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "{{Node1.response.data.name}} and {{simple}}",
                    }
                ],
            }
        ]
        assert _extract_variables(msgs) == ["Node1.response.data.name", "simple"]

    def test_bracket_notation_variable(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [{"type": "text", "text": "{{Node1.response[0].key}}"}],
            }
        ]
        assert _extract_variables(msgs) == ["Node1.response[0].key"]

    def test_dot_notation_with_whitespace(self):
        msgs = [
            {
                "id": "msg-0",
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "{{ Node1.response }} and {{ Node2.output }}",
                    }
                ],
            }
        ]
        assert _extract_variables(msgs) == ["Node1.response", "Node2.output"]


# ── _get_next_template_version ─────────────────────────────────────────


@pytest.mark.unit
class TestGetNextTemplateVersion:
    def test_first_version(self, db, organization, workspace):
        pt = PromptTemplate.no_workspace_objects.create(
            name="Fresh",
            organization=organization,
            workspace=workspace,
        )
        assert _get_next_template_version(pt) == "v1"

    def test_increments(self, db, prompt_template, prompt_version):
        # prompt_version is the first PV under prompt_template
        assert _get_next_template_version(prompt_template) == "v2"


# ── _build_prompt_config_snapshot ──────────────────────────────────────


@pytest.mark.unit
class TestBuildPromptConfigSnapshot:
    def test_nests_config_fields_under_configuration(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello {{name}}"}],
                }
            ],
            "model": "gpt-4o",
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)

        assert "configuration" in snapshot
        cfg = snapshot["configuration"]
        assert cfg["model"] == "gpt-4o"
        assert cfg["temperature"] == 0.7
        assert cfg["max_tokens"] == 1024
        assert cfg["response_format"] == "text"  # default

    def test_messages_at_top_level(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        assert snapshot["messages"] == [
            {"id": "msg-0", "role": "user", "content": [{"type": "text", "text": "hi"}]}
        ]

    def test_response_format_defaults_to_string(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ]
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        assert snapshot["configuration"]["response_format"] == "text"
        assert snapshot["response_format"] == "text"

    def test_includes_new_config_fields(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "output_format": "markdown",
            "tools": [{"type": "function", "function": {"name": "search"}}],
            "tool_choice": "auto",
            "model_detail": {"provider": "openai"},
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        cfg = snapshot["configuration"]
        assert cfg["output_format"] == "markdown"
        assert cfg["tools"][0]["function"]["name"] == "search"
        assert cfg["tool_choice"] == "auto"
        assert cfg["model_detail"]["provider"] == "openai"

    def test_none_values_excluded_from_configuration(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "model": None,
            "temperature": None,
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        cfg = snapshot["configuration"]
        assert "model" not in cfg
        assert "temperature" not in cfg

    def test_variable_names_in_snapshot(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello {{name}}"}],
                }
            ],
            "variable_names": {"name": ["Alice"]},
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        assert snapshot["variable_names"] == {"name": ["Alice"]}

    def test_variable_names_auto_extracted_when_not_provided(self):
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "{{a}} and {{b}}"}],
                }
            ],
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        assert snapshot["variable_names"] == {"a": [], "b": []}


# ── _dedupe_name ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestDedupeName:
    def test_no_collision(self):
        seen = set()
        assert _dedupe_name("port_a", seen) == "port_a"
        assert "port_a" in seen

    def test_collision_appends_suffix(self):
        seen = {"port_a"}
        assert _dedupe_name("port_a", seen) == "port_a_1"
        assert "port_a_1" in seen

    def test_multiple_collisions(self):
        seen = {"port_a", "port_a_1"}
        assert _dedupe_name("port_a", seen) == "port_a_2"

    def test_different_names_no_collision(self):
        seen = set()
        assert _dedupe_name("x", seen) == "x"
        assert _dedupe_name("y", seen) == "y"
        assert seen == {"x", "y"}


# ── _create_input_ports_from_prompt / _create_default_output_port ──────


@pytest.mark.unit
class TestCreateInputAndOutputPortsFromPrompt:
    def test_creates_only_input_ports(self, db, graph_version, llm_node_template):
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Test",
            config={},
            position={},
        )
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "{{a}} and {{b}}"}],
                },
            ],
        }
        _create_input_ports_from_prompt(node, prompt_data)

        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 2
        assert all(p.direction == PortDirection.INPUT for p in ports)
        names = set(ports.values_list("display_name", flat=True))
        assert names == {"a", "b"}

    def test_creates_default_output_port_string(
        self, db, graph_version, llm_node_template
    ):
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Test",
            config={},
            position={},
        )
        prompt_data = {"response_format": "text"}
        _create_default_output_port_from_prompt(node, prompt_data)

        port = Port.no_workspace_objects.get(node=node)
        assert port.direction == PortDirection.OUTPUT
        assert port.key == "response"
        assert port.data_schema == {"type": "string"}

    def test_creates_default_output_port_json(
        self, db, graph_version, llm_node_template
    ):
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Test",
            config={},
            position={},
        )
        prompt_data = {"response_format": "json"}
        _create_default_output_port_from_prompt(node, prompt_data)

        port = Port.no_workspace_objects.get(node=node)
        assert port.data_schema == {"type": "object"}

    def test_creates_default_output_port_json_schema(
        self, db, graph_version, llm_node_template
    ):
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Test",
            config={},
            position={},
        )
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        prompt_data = {
            "response_format": "json_schema",
            "response_schema": schema,
        }
        _create_default_output_port_from_prompt(node, prompt_data)

        port = Port.no_workspace_objects.get(node=node)
        assert port.data_schema == schema


# ── _validate_subgraph_fe_ports ────────────────────────────────────────


@pytest.mark.unit
class TestValidateSubgraphFePorts:
    def test_valid_ref_port_ids_pass(self, db, active_referenced_graph_version):
        exposed_port_id = uuid.uuid4()
        exposed = [
            {
                "id": exposed_port_id,
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
            }
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            ports_data = [
                {"id": uuid.uuid4(), "ref_port_id": exposed_port_id},
            ]
            # Should not raise
            _validate_subgraph_fe_ports(ports_data, active_referenced_graph_version)

    def test_invalid_ref_port_id_raises(self, db, active_referenced_graph_version):
        exposed = [
            {
                "id": uuid.uuid4(),
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
            }
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            ports_data = [
                {"id": uuid.uuid4(), "ref_port_id": uuid.uuid4()},  # non-matching
            ]
            with pytest.raises(ValidationError):
                _validate_subgraph_fe_ports(ports_data, active_referenced_graph_version)

    def test_no_ref_port_id_passes(self, db, active_referenced_graph_version):
        exposed = [
            {
                "id": uuid.uuid4(),
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
            }
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            ports_data = [{"id": uuid.uuid4()}]  # no ref_port_id
            # Should not raise
            _validate_subgraph_fe_ports(ports_data, active_referenced_graph_version)


# ── create_node: LLM node with FE output ports ────────────────────────


@pytest.mark.unit
class TestCreateNodeLLMWithFePorts:
    def test_llm_node_with_fe_output_ports(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """FE-supplied ports are created in addition to auto-created input ports."""
        fe_port_id = uuid.uuid4()
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM with FE ports",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Answer {{question}} with {{context}}",
                            }
                        ],
                    }
                ],
            },
            "ports": [
                {
                    "id": fe_port_id,
                    "key": "response",
                    "display_name": "llm_output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        output_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT
        )
        # Input ports auto-created from variables
        assert input_ports.count() == 2
        names = set(input_ports.values_list("display_name", flat=True))
        assert names == {"question", "context"}
        # Output port from FE
        assert output_ports.count() == 1
        assert output_ports.first().id == fe_port_id
        assert output_ports.first().display_name == "llm_output"

    def test_llm_node_without_ports_creates_default_output(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Without FE ports, a default 'response' output port is created."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM default",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{input}}"}],
                    }
                ],
            },
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        output_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT
        )
        assert output_ports.count() == 1
        assert output_ports.first().key == "response"
        assert output_ports.first().display_name == "response"


# ── create_node: LLM node without prompt_data ──────────────────────────


@pytest.mark.unit
class TestCreateLLMNodeWithoutPromptData:
    def test_llm_node_without_prompt_data_creates_ptn(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """LLM node created without prompt_template still gets PT/PTV/PTN."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Blank LLM",
            "node_template_id": llm_node_template.id,
            # no prompt_template key
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        assert ptn.prompt_template is not None
        assert ptn.prompt_version is not None
        assert ptn.prompt_version.is_draft is True
        # Snapshot should have empty messages
        assert ptn.prompt_version.prompt_config_snapshot["messages"] == []

    def test_llm_node_without_prompt_data_creates_default_output_port(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """LLM node without prompt_data still gets a default response output port."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Blank LLM",
            "node_template_id": llm_node_template.id,
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        output_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT
        )
        assert output_ports.count() == 1
        assert output_ports.first().key == "response"
        assert output_ports.first().data_schema == {"type": "string"}

    def test_llm_node_without_prompt_data_no_input_ports(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """LLM node without prompt_data creates no input ports (no variables)."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Blank LLM",
            "node_template_id": llm_node_template.id,
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 0

    def test_llm_node_with_null_prompt_data_creates_ptn(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """LLM node with explicit prompt_template=None still gets PT/PTV/PTN."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Null Prompt LLM",
            "node_template_id": llm_node_template.id,
            "prompt_template": None,
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        assert ptn.prompt_template is not None
        assert ptn.prompt_version is not None

    def test_non_llm_template_without_prompt_data_no_ptn(
        self, db, graph_version, node_template, user, organization, workspace
    ):
        """Non-LLM (STRICT) template node without prompt_data does NOT create PTN."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "Strict Node",
            "node_template_id": node_template.id,
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        assert not PromptTemplateNode.no_workspace_objects.filter(node=node).exists()


# ── create_node: subgraph without ref_graph_version_id ─────────────────


@pytest.mark.unit
class TestCreateSubgraphNodeWithoutRef:
    def test_subgraph_without_ref_creates_no_ports(
        self, db, graph_version, user, organization, workspace
    ):
        """Subgraph node without ref_graph_version_id creates no auto ports."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.SUBGRAPH,
            "name": "Empty Subgraph",
        }
        node, nc = create_node(graph_version, data, user, organization, workspace)

        assert node.type == NodeType.SUBGRAPH
        assert node.ref_graph_version is None
        assert nc is None
        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 0


# ── create_node: subgraph with FE ports ────────────────────────────────


@pytest.mark.unit
class TestCreateSubgraphNodeWithFePorts:
    def test_subgraph_with_fe_ports_and_validation(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
        node_template,
    ):
        """Subgraph node with FE ports validates ref_port_ids and creates ports."""
        # Create actual ports in the referenced graph version
        ref_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Ref Node",
            config={},
            position={},
        )
        exposed_input_port = Port(
            id=uuid.uuid4(),
            node=ref_node,
            key="input1",
            display_name="inp",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        exposed_input_port.save(skip_validation=True)

        exposed_output_port = Port(
            id=uuid.uuid4(),
            node=ref_node,
            key="output1",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        exposed_output_port.save(skip_validation=True)

        exposed = [
            {
                "id": exposed_input_port.id,
                "display_name": "inp",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "id": exposed_output_port.id,
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        fe_output_port_id = uuid.uuid4()
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            data = {
                "id": uuid.uuid4(),
                "type": NodeType.SUBGRAPH,
                "name": "Sub with FE ports",
                "ref_graph_version_id": active_referenced_graph_version.id,
                "ports": [
                    {
                        "id": fe_output_port_id,
                        "key": "custom",
                        "display_name": "custom_output",
                        "direction": PortDirection.OUTPUT,
                        "ref_port_id": exposed_output_port.id,
                    }
                ],
            }
            node, _ = create_node(graph_version, data, user, organization, workspace)

        # Input ports auto-created from exposed inputs
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 1
        assert input_ports.first().display_name == "inp"

        # Output port from FE array
        output_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT
        )
        assert output_ports.count() == 1
        assert output_ports.first().id == fe_output_port_id
        assert output_ports.first().display_name == "custom_output"

    def test_subgraph_without_fe_ports_auto_creates_outputs(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """Subgraph node without FE ports auto-creates output ports from exposed."""
        exposed = [
            {
                "display_name": "inp",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            data = {
                "id": uuid.uuid4(),
                "type": NodeType.SUBGRAPH,
                "name": "Sub auto",
                "ref_graph_version_id": active_referenced_graph_version.id,
            }
            node, _ = create_node(graph_version, data, user, organization, workspace)

        all_ports = Port.no_workspace_objects.filter(node=node)
        assert all_ports.count() == 2
        directions = set(all_ports.values_list("direction", flat=True))
        assert directions == {PortDirection.INPUT, PortDirection.OUTPUT}


# ── update_node: ref_graph_version_id ─────────────────────────────────


@pytest.mark.unit
class TestUpdateNodeRefGraphVersion:
    def test_update_sets_ref_graph_version(
        self,
        db,
        subgraph_node,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """PATCH with ref_graph_version_id sets the reference."""
        # Create a different graph with an active version to switch to
        from agent_playground.models.choices import GraphVersionStatus
        from agent_playground.models.graph import Graph
        from agent_playground.models.graph_version import GraphVersion

        new_graph = Graph.no_workspace_objects.create(
            name="Another Graph",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        new_gv = GraphVersion.no_workspace_objects.create(
            graph=new_graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
            tags=[],
        )
        updated = update_node(
            subgraph_node,
            {"ref_graph_version_id": new_gv.id},
            user,
            organization,
            workspace,
        )
        assert updated.ref_graph_version_id == new_gv.id

    def test_update_clears_ref_graph_version(
        self, db, subgraph_node, user, organization, workspace
    ):
        """PATCH with ref_graph_version_id=None clears the reference."""
        updated = update_node(
            subgraph_node,
            {"ref_graph_version_id": None},
            user,
            organization,
            workspace,
        )
        assert updated.ref_graph_version is None

    def test_update_with_ports_replaces_output_ports(
        self, db, graph_version, node_template, user, organization, workspace
    ):
        """PATCH with ports array replaces output ports while preserving input ports."""
        # Create node with both input and output ports
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Test Node",
            config={},
        )
        input_port = Port(
            node=node,
            key="input1",
            display_name="Input Port",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        input_port.save(skip_validation=True)
        old_output = Port(
            node=node,
            key="output1",
            display_name="Old Output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        old_output.save(skip_validation=True)

        # Update with new output ports only
        update_node(
            node,
            {
                "ports": [
                    {
                        "id": uuid.uuid4(),
                        "key": "new_output",
                        "display_name": "New Output",
                        "direction": PortDirection.OUTPUT,
                        "data_schema": {"type": "boolean"},
                    }
                ]
            },
            user,
            organization,
            workspace,
        )

        # Verify input port is preserved
        input_port.refresh_from_db()
        assert input_port.deleted is False
        assert input_port.display_name == "Input Port"

        # Verify old output port is deleted
        old_output.refresh_from_db()
        assert old_output.deleted is True

        # Verify new output port exists
        active_outputs = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT, deleted=False
        )
        assert active_outputs.count() == 1
        assert active_outputs.first().display_name == "New Output"


# ── _replace_output_ports ──────────────────────────────────────────────


@pytest.mark.unit
class TestReplaceOutputPorts:
    """Tests for _replace_output_ports() - output port replacement during updates."""

    def test_replaces_only_output_ports_preserves_input(
        self, db, graph_version, node_template
    ):
        """Test that only output ports are replaced, input ports preserved."""
        # Create node with input and output ports
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Test Node",
            config={},
        )
        input_port = Port(
            node=node,
            key="input1",
            display_name="Input Port",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        input_port.save(skip_validation=True)
        old_output = Port(
            node=node,
            key="output1",
            display_name="Old Output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        old_output.save(skip_validation=True)

        # Replace output ports only (send only output ports)
        new_ports_data = [
            {
                "id": uuid.uuid4(),
                "key": "output2",
                "display_name": "New Output",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "boolean"},
            },
        ]
        _replace_output_ports(node, new_ports_data)

        # Verify input port is PRESERVED
        input_port.refresh_from_db()
        assert input_port.deleted is False
        assert input_port.display_name == "Input Port"

        # Verify old output port is DELETED
        old_output.refresh_from_db()
        assert old_output.deleted is True

        # Verify new output port exists
        active_outputs = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT, deleted=False
        )
        assert active_outputs.count() == 1
        assert active_outputs.first().display_name == "New Output"

    def test_cascade_deletes_edges_on_old_output_ports(
        self, db, graph_version, node_template
    ):
        """Test that edges connected to old OUTPUT ports are soft-deleted."""
        # Create two nodes with ports
        node_a = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node A",
            config={},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node B",
            config={},
        )

        port_a_out = Port(
            node=node_a,
            key="output",
            display_name="Output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        port_a_out.save(skip_validation=True)
        port_b_in = Port(
            node=node_b,
            key="input",
            display_name="Input",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        port_b_in.save(skip_validation=True)

        # Create edge A → B
        nc = NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=node_a,
            target_node=node_b,
        )
        edge = Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=port_a_out,
            target_port=port_b_in,
        )

        # Replace Node A's output ports
        new_ports_data = [
            {
                "id": uuid.uuid4(),
                "key": "new_output",
                "display_name": "New Output",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "number"},
            }
        ]
        _replace_output_ports(node_a, new_ports_data)

        # Verify edge is soft-deleted
        edge.refresh_from_db()
        assert edge.deleted is True
        assert edge.deleted_at is not None

        # Verify NodeConnection still exists (not deleted)
        nc.refresh_from_db()
        assert nc.deleted is False

    def test_empty_ports_array_deletes_all_output_ports(
        self, db, graph_version, node_template
    ):
        """Test that passing empty ports array removes all OUTPUT ports but preserves INPUT."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Test Node",
            config={},
        )
        input_port = Port(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        input_port.save(skip_validation=True)
        output_port = Port(
            node=node,
            key="output1",
            display_name="Output 1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        output_port.save(skip_validation=True)

        # Replace with empty array (removes all outputs)
        _replace_output_ports(node, [])

        # Verify input port is PRESERVED
        input_port.refresh_from_db()
        assert input_port.deleted is False

        # Verify output port is DELETED
        output_port.refresh_from_db()
        assert output_port.deleted is True

    def test_replaces_multiple_output_ports(self, db, graph_version, node_template):
        """Test replacing multiple output ports at once."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Test Node",
            config={},
        )

        # Create multiple old output ports
        old_output_1 = Port(
            node=node,
            key="output1",
            display_name="Old Output 1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        old_output_1.save(skip_validation=True)
        old_output_2 = Port(
            node=node,
            key="output2",
            display_name="Old Output 2",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "number"},
        )
        old_output_2.save(skip_validation=True)

        # Replace with three new output ports
        new_ports_data = [
            {
                "id": uuid.uuid4(),
                "key": "new1",
                "display_name": "New Output 1",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "boolean"},
            },
            {
                "id": uuid.uuid4(),
                "key": "new2",
                "display_name": "New Output 2",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "array"},
            },
            {
                "id": uuid.uuid4(),
                "key": "new3",
                "display_name": "New Output 3",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "object"},
            },
        ]
        _replace_output_ports(node, new_ports_data)

        # Verify old ports are deleted
        old_output_1.refresh_from_db()
        old_output_2.refresh_from_db()
        assert old_output_1.deleted is True
        assert old_output_2.deleted is True

        # Verify new ports exist
        active_outputs = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT, deleted=False
        )
        assert active_outputs.count() == 3
        output_names = {p.display_name for p in active_outputs}
        assert output_names == {"New Output 1", "New Output 2", "New Output 3"}


# ── variable_names / metadata / commit_message propagation ─────────────


@pytest.mark.unit
class TestVariableNamesAndMetadataPropagation:
    def test_create_node_propagates_variable_names_and_metadata(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Variable names and metadata are stored on PT and PV."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Vars",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{a}} and {{b}}"}],
                    }
                ],
                "variable_names": {"a": ["sample_a"], "b": []},
                "metadata": {"source": "test"},
            },
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        pt = ptn.prompt_template
        pv = ptn.prompt_version

        assert pv.variable_names == {"a": ["sample_a"], "b": []}
        assert pv.metadata == {"source": "test"}
        assert set(pt.variable_names) == {"a", "b"}

    def test_create_node_auto_extracts_variable_names(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """When variable_names not provided, auto-extracted from messages."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Auto Vars",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{x}} plus {{y}}"}],
                    }
                ],
            },
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        pv = ptn.prompt_version
        assert pv.variable_names == {"x": [], "y": []}

    def test_update_node_commit_message_on_save(
        self,
        db,
        node,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        """Committing a draft version stores commit_message."""
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "commit this"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                    "save_prompt_version": True,
                    "commit_message": "First release",
                }
            },
            user,
            organization,
            workspace,
        )
        draft_prompt_version.refresh_from_db()
        assert draft_prompt_version.is_draft is False
        assert draft_prompt_version.commit_message == "First release"

    def test_update_node_variable_names_synced_to_pt(
        self,
        db,
        node,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        """Variable names synced to parent PromptTemplate on update."""
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{new_var}}"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                    "variable_names": {"new_var": ["sample"]},
                }
            },
            user,
            organization,
            workspace,
        )
        prompt_template.refresh_from_db()
        assert "new_var" in prompt_template.variable_names

    def test_create_node_adds_user_as_collaborator(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """User is added as collaborator on newly created PromptTemplate."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM Collab",
            "node_template_id": llm_node_template.id,
            "prompt_template": _base_prompt_data(),
        }
        node, _ = create_node(graph_version, data, user, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        pt = ptn.prompt_template
        assert user in pt.collaborators.all()

    def test_create_node_with_user_none_no_collaborator(
        self, db, graph_version, llm_node_template, organization, workspace
    ):
        """When user is None, no collaborator is added (no crash)."""
        data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "LLM No User",
            "node_template_id": llm_node_template.id,
            "prompt_template": _base_prompt_data(),
        }
        node, _ = create_node(graph_version, data, None, organization, workspace)

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        pt = ptn.prompt_template
        assert pt.collaborators.count() == 0


# ── _output_data_schema ────────────────────────────────────────────────


@pytest.mark.unit
class TestOutputDataSchema:
    def test_string_format(self):
        assert _output_data_schema("text") == {"type": "string"}

    def test_json_format(self):
        assert _output_data_schema("json") == {"type": "object"}

    def test_json_schema_with_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        assert _output_data_schema("json_schema", schema) == schema

    def test_json_schema_without_schema_falls_back_to_string(self):
        assert _output_data_schema("json_schema") == {"type": "string"}

    def test_json_schema_with_none_schema_falls_back_to_string(self):
        assert _output_data_schema("json_schema", None) == {"type": "string"}

    def test_unknown_format_falls_back_to_string(self):
        assert _output_data_schema("xml") == {"type": "string"}


# ── _create_input_ports_from_prompt edge cases ─────────────────────────


@pytest.mark.unit
class TestCreateInputPortsFromPromptEdgeCases:
    def test_no_variables_creates_no_ports(self, db, graph_version, llm_node_template):
        """Messages without {{variables}} create no input ports."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="No Vars",
            config={},
            position={},
        )
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "Just plain text"}],
                }
            ],
        }
        _create_input_ports_from_prompt(node, prompt_data)

        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 0

    def test_empty_messages_creates_no_ports(
        self, db, graph_version, llm_node_template
    ):
        """Empty messages list creates no input ports."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="Empty Msgs",
            config={},
            position={},
        )
        _create_input_ports_from_prompt(node, {"messages": []})

        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 0


# ── _create_subgraph_input_ports / _create_subgraph_output_ports ───────


@pytest.mark.unit
class TestCreateSubgraphInputOutputPorts:
    def test_creates_only_input_ports(
        self, db, graph_version, active_referenced_graph_version
    ):
        """_create_subgraph_input_ports only creates INPUT ports."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Sub Input",
            config={},
            position={},
            ref_graph_version=active_referenced_graph_version,
        )
        exposed = [
            {
                "display_name": "inp1",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "display_name": "out1",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            _create_subgraph_input_ports(node, active_referenced_graph_version)

        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 1
        assert ports.first().direction == PortDirection.INPUT
        assert ports.first().display_name == "inp1"

    def test_creates_only_output_ports(
        self, db, graph_version, active_referenced_graph_version
    ):
        """_create_subgraph_output_ports only creates OUTPUT ports."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Sub Output",
            config={},
            position={},
            ref_graph_version=active_referenced_graph_version,
        )
        exposed = [
            {
                "display_name": "inp1",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "display_name": "out1",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            _create_subgraph_output_ports(node, active_referenced_graph_version)

        ports = Port.no_workspace_objects.filter(node=node)
        assert ports.count() == 1
        assert ports.first().direction == PortDirection.OUTPUT
        assert ports.first().display_name == "out1"

    def test_dedupes_duplicate_display_names(
        self, db, graph_version, active_referenced_graph_version
    ):
        """Duplicate display names among exposed ports get suffixed."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Sub Dedup",
            config={},
            position={},
            ref_graph_version=active_referenced_graph_version,
        )
        exposed = [
            {
                "display_name": "text",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
            {
                "display_name": "text",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            _create_subgraph_input_ports(node, active_referenced_graph_version)

        names = set(
            Port.no_workspace_objects.filter(node=node).values_list(
                "display_name", flat=True
            )
        )
        assert names == {"text", "text_1"}

    def test_sets_ref_port_id(
        self, db, graph_version, active_referenced_graph_version, node_template
    ):
        """Created ports have ref_port_id set from exposed port id."""
        # Create a port in the referenced graph version that can be referenced
        ref_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Ref Node",
            config={},
            position={},
        )
        ref_port = Port(
            id=uuid.uuid4(),
            node=ref_node,
            key="output1",
            display_name="out",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        ref_port.save(skip_validation=True)

        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Sub Ref",
            config={},
            position={},
            ref_graph_version=active_referenced_graph_version,
        )
        exposed = [
            {
                "id": ref_port.id,
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            _create_subgraph_output_ports(node, active_referenced_graph_version)

        port = Port.no_workspace_objects.get(node=node, display_name="out")
        assert port.ref_port_id == ref_port.id

    def test_empty_exposed_ports_creates_nothing(
        self, db, graph_version, active_referenced_graph_version
    ):
        """No exposed ports means no ports created."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.SUBGRAPH,
            name="Sub Empty",
            config={},
            position={},
            ref_graph_version=active_referenced_graph_version,
        )
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: []},
        ):
            _create_subgraph_input_ports(node, active_referenced_graph_version)
            _create_subgraph_output_ports(node, active_referenced_graph_version)

        assert Port.no_workspace_objects.filter(node=node).count() == 0


# ── update_node: prompt_template when no PTN exists ────────────────────


@pytest.mark.unit
class TestUpdateNodeWithoutExistingPTN:
    def test_update_creates_ptn_from_scratch(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """PATCH with prompt_template on a node without PTN creates one."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="No PTN Node",
            config={},
            position={},
        )
        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{query}}"}],
                        }
                    ],
                }
            },
            user,
            organization,
            workspace,
        )

        ptn = PromptTemplateNode.no_workspace_objects.get(node=node)
        assert ptn.prompt_template is not None
        assert ptn.prompt_version is not None
        assert ptn.prompt_version.is_draft is True


# ── update_node: save_version=True committed PV propagates metadata ────


@pytest.mark.unit
class TestUpdateNodeSaveVersionCommittedPV:
    def test_save_committed_creates_new_committed_with_metadata(
        self,
        db,
        node,
        prompt_template,
        prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        """save_prompt_version=True + committed PV creates new committed PV with metadata."""
        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{x}}"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": prompt_version.id,
                    "save_prompt_version": True,
                    "variable_names": {"x": ["sample"]},
                    "metadata": {"env": "prod"},
                    "commit_message": "v2 release",
                }
            },
            user,
            organization,
            workspace,
        )
        prompt_template_node.refresh_from_db()
        new_pv = prompt_template_node.prompt_version
        assert new_pv.id != prompt_version.id
        assert new_pv.is_draft is False
        assert new_pv.variable_names == {"x": ["sample"]}
        assert new_pv.metadata == {"env": "prod"}
        assert new_pv.commit_message == "v2 release"

    def test_save_false_committed_creates_draft_with_metadata(
        self,
        db,
        node,
        prompt_template,
        prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        """save_prompt_version=False + committed PV creates new draft PV with metadata."""
        update_node(
            node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{y}}"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": prompt_version.id,
                    "save_prompt_version": False,
                    "variable_names": {"y": []},
                    "metadata": {"env": "staging"},
                }
            },
            user,
            organization,
            workspace,
        )
        prompt_template_node.refresh_from_db()
        new_pv = prompt_template_node.prompt_version
        assert new_pv.id != prompt_version.id
        assert new_pv.is_draft is True
        assert new_pv.variable_names == {"y": []}
        assert new_pv.metadata == {"env": "staging"}


# ── update_node: name sync to PromptTemplate ──────────────────────────


@pytest.mark.unit
class TestUpdateNodeNameSyncToPT:
    def test_name_update_syncs_to_prompt_template(
        self,
        db,
        node,
        prompt_template,
        prompt_version,
        prompt_template_node,
        user,
        organization,
        workspace,
    ):
        """Updating node name syncs to linked PromptTemplate.name."""
        update_node(
            node,
            {"name": "New PT Name"},
            user,
            organization,
            workspace,
        )
        prompt_template.refresh_from_db()
        assert prompt_template.name == "New PT Name"


# ── create_node: subgraph with invalid FE ref_port_id ──────────────────


@pytest.mark.unit
class TestCreateSubgraphInvalidRefPortId:
    def test_invalid_ref_port_id_raises_validation_error(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        user,
        organization,
        workspace,
    ):
        """create_node raises when FE ports have invalid ref_port_id."""
        exposed = [
            {
                "id": uuid.uuid4(),
                "display_name": "out",
                "direction": PortDirection.OUTPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            data = {
                "id": uuid.uuid4(),
                "type": NodeType.SUBGRAPH,
                "name": "Bad Ref",
                "ref_graph_version_id": active_referenced_graph_version.id,
                "ports": [
                    {
                        "id": uuid.uuid4(),
                        "key": "custom",
                        "display_name": "bad",
                        "direction": PortDirection.OUTPUT,
                        "ref_port_id": uuid.uuid4(),  # non-matching
                    }
                ],
            }
            with pytest.raises(ValidationError):
                create_node(graph_version, data, user, organization, workspace)


# ── _build_prompt_config_snapshot: comprehensive config keys ───────────


@pytest.mark.unit
class TestBuildPromptConfigSnapshotAllKeys:
    def test_all_config_keys_nested(self):
        """All recognized config keys are placed under configuration."""
        prompt_data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "hi"}],
                }
            ],
            "model": "gpt-4o",
            "temperature": 0.8,
            "max_tokens": 2048,
            "top_p": 0.95,
            "frequency_penalty": 0.3,
            "presence_penalty": 0.4,
            "response_format": "json_schema",
            "response_schema": {"type": "object"},
            "output_format": "json",
            "tools": [{"type": "function", "function": {"name": "f"}}],
            "tool_choice": {"type": "function", "function": {"name": "f"}},
            "model_detail": {"provider": "openai", "version": "2024-01"},
        }
        snapshot = _build_prompt_config_snapshot(prompt_data)
        cfg = snapshot["configuration"]

        assert cfg["model"] == "gpt-4o"
        assert cfg["temperature"] == 0.8
        assert cfg["max_tokens"] == 2048
        assert cfg["top_p"] == 0.95
        assert cfg["frequency_penalty"] == 0.3
        assert cfg["presence_penalty"] == 0.4
        assert cfg["response_format"] == "json_schema"
        assert cfg["response_schema"] == {"type": "object"}
        assert cfg["output_format"] == "json"
        assert len(cfg["tools"]) == 1
        assert cfg["tool_choice"]["type"] == "function"
        assert cfg["model_detail"]["provider"] == "openai"

        # Top-level compat keys
        assert snapshot["response_format"] == "json_schema"
        assert snapshot["response_schema"] == {"type": "object"}

    def test_empty_prompt_data_has_minimal_snapshot(self):
        """Minimal prompt_data produces minimal valid snapshot."""
        prompt_data = {"messages": []}
        snapshot = _build_prompt_config_snapshot(prompt_data)

        assert snapshot["messages"] == []
        assert snapshot["configuration"]["response_format"] == "text"
        assert snapshot["variable_names"] == {}


# ── _resolve_or_create_pt_ptv: add collaborator on existing PT ─────────


@pytest.mark.unit
class TestResolveOrCreatePtPtvCollaborator:
    def test_existing_pt_adds_collaborator(
        self, db, prompt_template, user, organization, workspace
    ):
        """When creating new PV under existing PT, user is added as collaborator."""
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "{{var}}"}],
                }
            ],
            "prompt_template_id": prompt_template.id,
        }
        from agent_playground.services.node_crud import _resolve_or_create_pt_ptv

        pt, pv = _resolve_or_create_pt_ptv(data, "Test", user, organization, workspace)
        assert user in pt.collaborators.all()
        assert pv.is_draft is True

    def test_existing_pt_user_none_no_crash(
        self, db, prompt_template, organization, workspace
    ):
        """When user is None on existing PT, no crash and no collaborator added."""
        data = {
            "messages": [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": "{{var}}"}],
                }
            ],
            "prompt_template_id": prompt_template.id,
        }
        from agent_playground.services.node_crud import _resolve_or_create_pt_ptv

        pt, pv = _resolve_or_create_pt_ptv(data, "Test", None, organization, workspace)
        assert pt.collaborators.count() == 0


# ── Auto-create edges for node references in prompts ───────────────────


@pytest.mark.unit
class TestAutoCreateEdgesForNode:
    def test_create_node_with_dot_notation_creates_edge(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Creating LLM node with dot-notation variable creates edge from referenced node."""
        # Create source node with output port "llm_output"
        source_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_3",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "llm_output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_node_data, user, organization, workspace
        )

        # Create target node that references node_3.llm_output WITH source_node_id
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_5",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node.id,  # Creates NodeConnection
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze {{node_3.llm_output}} and answer {{question}}",
                            }
                        ],
                    }
                ],
            },
        }
        target_node, nc = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Verify NodeConnection was created
        assert nc is not None
        assert nc.source_node == source_node
        assert nc.target_node == target_node

        # Verify input ports were created
        input_ports = Port.no_workspace_objects.filter(
            node=target_node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 2
        port_names = set(input_ports.values_list("display_name", flat=True))
        assert port_names == {"node_3.llm_output", "question"}

        # Verify edge was created from node_3's llm_output to node_5's node_3.llm_output input
        source_port = Port.no_workspace_objects.get(
            node=source_node, display_name="llm_output"
        )
        target_port = Port.no_workspace_objects.get(
            node=target_node, display_name="node_3.llm_output"
        )
        edge = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port=source_port,
            target_port=target_port,
        ).first()
        assert edge is not None
        assert edge.source_port == source_port
        assert edge.target_port == target_port

    def test_create_node_with_multiple_node_references_creates_multiple_edges(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Creating node with multiple node references creates multiple edges."""
        # Create two source nodes
        source_node_1_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_1",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node_1, _ = create_node(
            graph_version, source_node_1_data, user, organization, workspace
        )

        source_node_2_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_2",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "result",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node_2, _ = create_node(
            graph_version, source_node_2_data, user, organization, workspace
        )

        # Create NodeConnections manually for both sources to the target
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_3",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node_1.id,  # Creates NC from node_1
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Combine {{node_1.output}} and {{node_2.result}}",
                            }
                        ],
                    }
                ],
            },
        }
        target_node, _ = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Create second NodeConnection manually
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=source_node_2,
            target_node=target_node,
        )

        # Re-run edge creation after adding second NodeConnection
        from agent_playground.services.node_crud import _auto_create_edges_for_node

        _auto_create_edges_for_node(target_node)

        # Verify both edges were created
        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 2

        # Verify edge from node_1.output to node_3's node_1.output input
        source_port_1 = Port.no_workspace_objects.get(
            node=source_node_1, display_name="output"
        )
        target_port_1 = Port.no_workspace_objects.get(
            node=target_node, display_name="node_1.output"
        )
        edge_1 = Edge.no_workspace_objects.filter(
            source_port=source_port_1, target_port=target_port_1
        ).first()
        assert edge_1 is not None

        # Verify edge from node_2.result to node_3's node_2.result input
        source_port_2 = Port.no_workspace_objects.get(
            node=source_node_2, display_name="result"
        )
        target_port_2 = Port.no_workspace_objects.get(
            node=target_node, display_name="node_2.result"
        )
        edge_2 = Edge.no_workspace_objects.filter(
            source_port=source_port_2, target_port=target_port_2
        ).first()
        assert edge_2 is not None

    def test_create_node_without_node_connection_no_edge(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Creating node without NodeConnection doesn't create edge even if node referenced."""
        # Create source node
        source_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_1",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_node_data, user, organization, workspace
        )

        # Create target node that references node_1.output but WITHOUT source_node_id
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_2",
            "node_template_id": llm_node_template.id,
            # No source_node_id provided → no NodeConnection
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Use {{node_1.output}}"}],
                    },
                ],
            },
        }
        target_node, nc = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Verify no NodeConnection was created
        assert nc is None

        # Verify input port was created for node_1.output
        input_port = Port.no_workspace_objects.filter(
            node=target_node, display_name="node_1.output"
        ).first()
        assert input_port is not None

        # Verify NO edge was created (because no NodeConnection exists)
        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0

    def test_update_prompt_template_creates_new_edges(
        self,
        db,
        graph_version,
        llm_node_template,
        prompt_template,
        draft_prompt_version,
        user,
        organization,
        workspace,
    ):
        """Updating prompt template to add node reference creates new edge."""
        # Create source node with output port
        source_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "source",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "data",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_node_data, user, organization, workspace
        )

        # Create target node with initial prompt (no node references)
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "target",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node.id,  # Create NodeConnection
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Original {{input}}"}],
                    }
                ],
                "prompt_template_id": prompt_template.id,
                "prompt_version_id": draft_prompt_version.id,
            },
        }
        target_node, nc = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Verify no edges initially (input port "input" doesn't match source port "data")
        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0

        # Update prompt template to reference source.data
        update_node(
            target_node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Updated {{source.data}}"}
                            ],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                }
            },
            user,
            organization,
            workspace,
        )

        # Verify new input port was created
        input_port = Port.no_workspace_objects.filter(
            node=target_node, display_name="source.data"
        ).first()
        assert input_port is not None

        # Verify edge was created
        source_port = Port.no_workspace_objects.get(
            node=source_node, display_name="data"
        )
        edge = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port=source_port,
            target_port=input_port,
        ).first()
        assert edge is not None

    def test_no_edge_when_output_port_not_found(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """No edge created when referenced output port doesn't exist on source node."""
        # Create source node with output port "response"
        source_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_1",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "response",  # Different from what target references
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_node_data, user, organization, workspace
        )

        # Create target node that references node_1.wrong_port (doesn't exist)
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "node_2",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node.id,  # Creates NodeConnection
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Use {{node_1.wrong_port}}"}
                        ],
                    },
                ],
            },
        }
        target_node, nc = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Verify NodeConnection was created
        assert nc is not None

        # Verify input port was created
        input_port = Port.no_workspace_objects.filter(
            node=target_node, display_name="node_1.wrong_port"
        ).first()
        assert input_port is not None

        # Verify NO edge was created (output port "wrong_port" doesn't exist)
        edges = Edge.no_workspace_objects.filter(graph_version=graph_version)
        assert edges.count() == 0

    def test_simple_variable_matching_creates_edge(
        self, db, graph_version, llm_node_template, user, organization, workspace
    ):
        """Simple variable (no dot-notation) creates edge when output port matches."""
        # Create source node with output port "context"
        source_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "source",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "context",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_node_data, user, organization, workspace
        )

        # Create target node with simple variable {{context}}
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "target",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node.id,  # Creates NodeConnection
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Answer with {{context}}"}
                        ],
                    }
                ],
            },
        }
        target_node, nc = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Verify edge was created
        source_port = Port.no_workspace_objects.get(
            node=source_node, display_name="context"
        )
        target_port = Port.no_workspace_objects.get(
            node=target_node, display_name="context"
        )
        edge = Edge.no_workspace_objects.filter(
            graph_version=graph_version,
            source_port=source_port,
            target_port=target_port,
        ).first()
        assert edge is not None

    def test_update_prompt_removes_old_edge(
        self,
        db,
        graph_version,
        llm_node_template,
        prompt_template,
        draft_prompt_version,
        user,
        organization,
        workspace,
    ):
        """Updating prompt to remove variable soft-deletes old port and edge."""
        # Create source node
        source_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "source",
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": uuid.uuid4(),
                    "key": "response",
                    "display_name": "data",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                }
            ],
        }
        source_node, _ = create_node(
            graph_version, source_node_data, user, organization, workspace
        )

        # Create target node with reference to source.data
        target_node_data = {
            "id": uuid.uuid4(),
            "type": NodeType.ATOMIC,
            "name": "target",
            "node_template_id": llm_node_template.id,
            "source_node_id": source_node.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Use {{source.data}}"}],
                    }
                ],
                "prompt_template_id": prompt_template.id,
                "prompt_version_id": draft_prompt_version.id,
            },
        }
        target_node, _ = create_node(
            graph_version, target_node_data, user, organization, workspace
        )

        # Verify edge was created
        initial_edges = Edge.no_workspace_objects.filter(
            graph_version=graph_version, deleted=False
        )
        assert initial_edges.count() == 1

        # Update prompt to remove the variable
        update_node(
            target_node,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "No variables"}],
                        }
                    ],
                    "prompt_template_id": prompt_template.id,
                    "prompt_version_id": draft_prompt_version.id,
                }
            },
            user,
            organization,
            workspace,
        )

        # Verify old input port was soft-deleted
        old_port = Port.all_objects.filter(
            node=target_node, display_name="source.data"
        ).first()
        assert old_port is not None
        assert old_port.deleted is True

        # Verify edge was soft-deleted
        edges = Edge.no_workspace_objects.filter(
            graph_version=graph_version, deleted=False
        )
        assert edges.count() == 0

    def test_cascade_soft_delete_via_prompt_template_deletion(
        self, organization, workspace, user, graph_version, llm_node_template
    ):
        """Test that deleting a PromptTemplate cascades to all linked nodes."""
        # Create a prompt template
        pt = PromptTemplate.objects.create(
            name="Test Template",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="1",
            prompt_config_snapshot={"messages": [], "configuration": {}},
            is_draft=True,  # Must be draft so create_node will use it instead of creating new version
        )

        # Create nodes linked to this template
        node1_data = {
            "id": uuid.uuid4(),
            "name": "Node1",
            "type": NodeType.ATOMIC,
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
                "prompt_template_id": pt.id,
                "prompt_version_id": pv.id,
            },
        }
        node1, _ = create_node(graph_version, node1_data, user, organization, workspace)

        node2_data = {
            "id": uuid.uuid4(),
            "name": "Node2",
            "type": NodeType.ATOMIC,
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
                "prompt_template_id": pt.id,
                "prompt_version_id": pv.id,
            },
        }
        node2, _ = create_node(graph_version, node2_data, user, organization, workspace)

        # Verify nodes and PTN records exist
        assert Node.no_workspace_objects.filter(id=node1.id, deleted=False).exists()
        assert Node.no_workspace_objects.filter(id=node2.id, deleted=False).exists()
        assert PromptTemplateNode.no_workspace_objects.filter(
            node=node1, deleted=False
        ).exists()
        assert PromptTemplateNode.no_workspace_objects.filter(
            node=node2, deleted=False
        ).exists()

        # Soft delete the template
        pt.deleted = True
        pt.save()

        # Both nodes should be soft-deleted
        node1.refresh_from_db()
        node2.refresh_from_db()
        assert node1.deleted is True
        assert node2.deleted is True

        # PTN records should be soft-deleted
        ptn1 = PromptTemplateNode.all_objects.get(node=node1)
        ptn2 = PromptTemplateNode.all_objects.get(node=node2)
        assert ptn1.deleted is True
        assert ptn2.deleted is True

    def test_cascade_soft_delete_via_prompt_version_deletion(
        self, organization, workspace, user, graph_version, llm_node_template
    ):
        """Test that deleting a PromptVersion cascades to all linked nodes."""
        # Create a prompt template
        pt = PromptTemplate.objects.create(
            name="Test Template",
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version="1",
            prompt_config_snapshot={"messages": [], "configuration": {}},
            is_draft=True,  # Must be draft so create_node will use it instead of creating new version
        )

        # Create node linked to this version
        node_data = {
            "id": uuid.uuid4(),
            "name": "Node1",
            "type": NodeType.ATOMIC,
            "node_template_id": llm_node_template.id,
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Test"}],
                    }
                ],
                "prompt_template_id": pt.id,
                "prompt_version_id": pv.id,
            },
        }
        node, _ = create_node(graph_version, node_data, user, organization, workspace)

        # Verify node and PTN record exist
        assert Node.no_workspace_objects.filter(id=node.id, deleted=False).exists()
        assert PromptTemplateNode.no_workspace_objects.filter(
            node=node, deleted=False
        ).exists()

        # Soft delete the version
        pv = PromptVersion.all_objects.get(pk=pv.pk)
        pv.deleted = True
        pv.save()

        # Node should be soft-deleted
        node.refresh_from_db()
        assert node.deleted is True

        # PTN record should be soft-deleted
        ptn = PromptTemplateNode.all_objects.get(node=node)
        assert ptn.deleted is True

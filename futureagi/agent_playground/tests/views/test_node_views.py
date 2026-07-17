"""Tests for NodeCrudViewSet — granular node endpoints."""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status

from agent_playground.models.choices import NodeType, PortDirection, PortMode
from agent_playground.models.edge import Edge
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port

# ── helpers ────────────────────────────────────────────────────────────


def _node_create_url(graph, version):
    return reverse(
        "graph-version-node-create",
        kwargs={"pk": graph.id, "version_id": version.id},
    )


def _node_detail_url(graph, version, node_id):
    return reverse(
        "graph-version-node-detail",
        kwargs={"pk": graph.id, "version_id": version.id, "node_id": node_id},
    )


def _node_test_execution_url(graph, version, node_id):
    return reverse(
        "node-test-execution",
        kwargs={"pk": graph.id, "version_id": version.id, "node_id": node_id},
    )


# =====================================================================
# CREATE NODE
# =====================================================================


@pytest.mark.unit
class TestCreateNodeAPI:
    def test_create_atomic_node(
        self, authenticated_client, graph, graph_version, node_template
    ):
        fe_id = str(uuid.uuid4())
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": fe_id,
            "type": "atomic",
            "name": "New Node",
            "node_template_id": str(node_template.id),
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["status"] is True
        assert response.data["result"]["id"] == fe_id
        assert response.data["result"]["type"] == "atomic"

    def test_create_atomic_node_preserves_config(
        self, authenticated_client, graph, graph_version
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
                },
            },
        )
        config = {"language": "python", "code": "result = inputs"}
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Code Node",
            "node_template_id": str(template.id),
            "config": config,
        }

        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["config"] == config

    def test_create_llm_node_auto_creates_pt_pv(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "LLM Node",
            "node_template_id": str(llm_node_template.id),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Say {{greeting}}"}],
                    }
                ],
            },
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        result = response.data["result"]
        assert result["prompt_template"] is not None
        assert result["prompt_template"]["prompt_template_id"] is not None
        assert result["prompt_template"]["prompt_version_id"] is not None

    def test_create_node_with_source_creates_nc(
        self, authenticated_client, graph, graph_version, node_template, node
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node B",
            "node_template_id": str(node_template.id),
            "source_node_id": str(node.id),
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        result = response.data["result"]
        assert result["node_connection"] is not None
        assert str(result["node_connection"]["source_node_id"]) == str(node.id)

    def test_create_subgraph_node(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_referenced_graph_version,
    ):
        from unittest.mock import patch

        exposed = [
            {
                "display_name": "inp",
                "direction": PortDirection.INPUT,
                "data_schema": {"type": "string"},
            },
        ]
        with patch(
            "agent_playground.services.node_crud.get_exposed_ports_for_versions",
            return_value={active_referenced_graph_version.id: exposed},
        ):
            url = _node_create_url(graph, graph_version)
            payload = {
                "id": str(uuid.uuid4()),
                "type": "subgraph",
                "name": "Sub Node",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
            }
            response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["result"]["type"] == "subgraph"

    def test_create_node_rejects_non_draft(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_template,
    ):
        url = _node_create_url(graph, active_graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(node_template.id),
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False

    def test_create_node_invalid_payload(
        self, authenticated_client, graph, graph_version
    ):
        url = _node_create_url(graph, graph_version)
        # Missing required fields
        payload = {"name": "Bad"}
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_node_graph_not_found(self, authenticated_client, graph_version):
        fake_graph_id = uuid.uuid4()
        url = reverse(
            "graph-version-node-create",
            kwargs={"pk": fake_graph_id, "version_id": graph_version.id},
        )
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(uuid.uuid4()),
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_node_version_not_found(self, authenticated_client, graph):
        fake_version_id = uuid.uuid4()
        url = reverse(
            "graph-version-node-create",
            kwargs={"pk": graph.id, "version_id": fake_version_id},
        )
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(uuid.uuid4()),
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_create_node_requires_auth(
        self, api_client, graph, graph_version, node_template
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Node",
            "node_template_id": str(node_template.id),
        }
        response = api_client.post(url, payload, format="json")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_create_subgraph_node_with_input_mappings_e2e(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_referenced_graph_version,
        node_template,
    ):
        """E2E: POST granular create with input_mappings creates ports and edges."""
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
        source_port = Port(
            node=source_node,
            key="custom",
            display_name="output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        source_port.save(skip_validation=True)

        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "subgraph",
            "name": "Pipeline",
            "ref_graph_version_id": str(active_referenced_graph_version.id),
            "source_node_id": str(source_node.id),  # Create NodeConnection
            "input_mappings": [
                {"key": "context", "value": "DataLoader.output"},
                {"key": "question", "value": None},
            ],
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        result = response.data["result"]
        assert result["id"] == payload["id"]
        assert result["type"] == "subgraph"

        # Verify ports created
        node = Node.no_workspace_objects.get(id=payload["id"])
        input_ports = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 2
        port_names = set(input_ports.values_list("display_name", flat=True))
        assert port_names == {"context", "question"}

    def test_create_subgraph_with_source_node_and_input_mappings_creates_edges(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_referenced_graph_version,
        llm_node_template,
    ):
        """E2E: Create subgraph with source_node_id + input_mappings creates
        NodeConnection and edges from the caller-provided connection."""
        # Create an LLM source node first
        source_url = _node_create_url(graph, graph_version)
        source_id = str(uuid.uuid4())
        source_resp = authenticated_client.post(
            source_url,
            {
                "id": source_id,
                "type": "atomic",
                "name": "llm_node_a1",
                "node_template_id": str(llm_node_template.id),
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "{{context}}"}],
                        }
                    ],
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
            },
            format="json",
        )
        assert source_resp.status_code == status.HTTP_201_CREATED

        # Create subgraph node WITH source_node_id so NodeConnection is created
        sub_id = str(uuid.uuid4())
        url = _node_create_url(graph, graph_version)
        response = authenticated_client.post(
            url,
            {
                "id": sub_id,
                "type": "subgraph",
                "name": "Summary Pipeline",
                "ref_graph_version_id": str(active_referenced_graph_version.id),
                "source_node_id": source_id,
                "input_mappings": [
                    {"key": "context", "value": "llm_node_a1.llm_output"},
                    {"key": "question", "value": None},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED

        # Verify ports created
        sub_node = Node.no_workspace_objects.get(id=sub_id)
        input_ports = Port.no_workspace_objects.filter(
            node=sub_node, direction=PortDirection.INPUT
        )
        assert input_ports.count() == 2

        # Verify NodeConnection was created via source_node_id
        source_node = Node.no_workspace_objects.get(id=source_id)
        nc_exists = NodeConnection.no_workspace_objects.filter(
            source_node=source_node, target_node=sub_node
        ).exists()
        assert nc_exists, "NodeConnection should exist from source_node_id"

        # Verify edge created for mapped input (context -> llm_node_a1.llm_output)
        edges = Edge.no_workspace_objects.filter(target_port__node=sub_node)
        assert edges.count() == 1, f"Expected 1 edge, got {edges.count()}"
        assert edges.first().source_port.display_name == "llm_output"
        assert edges.first().target_port.display_name == "context"

    def test_create_atomic_node_with_input_mappings_validation_error(
        self,
        authenticated_client,
        graph,
        graph_version,
        node_template,
    ):
        """E2E: Atomic node with input_mappings returns 400 validation error."""
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Atomic Node",
            "node_template_id": str(node_template.id),
            "input_mappings": [
                {"key": "context", "value": "Source.output"}
            ],  # Invalid for atomic
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "input_mappings" in response.data.get("result", {})


# =====================================================================
# RETRIEVE NODE
# =====================================================================


@pytest.mark.unit
class TestRetrieveNodeAPI:
    def test_retrieve_existing_node(
        self, authenticated_client, graph, graph_version, node
    ):
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["id"] == str(node.id)
        assert response.data["result"]["name"] == node.name

    def test_retrieve_with_prompt_template(
        self,
        authenticated_client,
        graph,
        graph_version,
        node,
        prompt_template_node,
    ):
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["prompt_template"] is not None

    def test_retrieve_node_not_found(self, authenticated_client, graph, graph_version):
        fake_id = uuid.uuid4()
        url = _node_detail_url(graph, graph_version, fake_id)
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_node_wrong_version(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_graph_version,
        node,
    ):
        # node belongs to graph_version, but we query via active_graph_version
        url = _node_detail_url(graph, active_graph_version, node.id)
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_subgraph_with_input_mappings(
        self,
        authenticated_client,
        graph,
        graph_version,
        subgraph_node,
        node_template,
    ):
        """GET subgraph node returns reconstructed input_mappings."""
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
        source_port = Port(
            node=source_node,
            key="custom",
            display_name="output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        source_port.save(skip_validation=True)

        # Create input ports on subgraph node (one mapped, one unmapped)
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

        # NodeConnection required before creating edge
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=source_node,
            target_node=subgraph_node,
        )

        # Create edge: source_port → mapped_port
        Edge.no_workspace_objects.create(
            graph_version=graph_version,
            source_port=source_port,
            target_port=mapped_port,
        )

        url = _node_detail_url(graph, graph_version, subgraph_node.id)
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["input_mappings"] is not None
        # Convert list to dict for easier testing
        mappings = {m["key"]: m["value"] for m in result["input_mappings"]}
        assert mappings["context"] == "DataLoader.output"
        assert mappings["question"] is None

    def test_retrieve_atomic_node_input_mappings_is_none(
        self, authenticated_client, graph, graph_version, node
    ):
        """GET atomic node returns input_mappings as None."""
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["input_mappings"] is None


# =====================================================================
# UPDATE NODE
# =====================================================================


@pytest.mark.unit
class TestUpdateNodeAPI:
    def test_update_name(self, authenticated_client, graph, graph_version, node):
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.patch(url, {"name": "Renamed"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["name"] == "Renamed"

    def test_update_position(self, authenticated_client, graph, graph_version, node):
        url = _node_detail_url(graph, graph_version, node.id)
        new_pos = {"x": 999, "y": 111}
        response = authenticated_client.patch(url, {"position": new_pos}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["position"] == new_pos

    def test_update_prompt_template(
        self,
        authenticated_client,
        graph,
        graph_version,
        node,
        llm_node_template,
        prompt_template,
        draft_prompt_version,
        prompt_template_node,
    ):
        # Re-point node to llm_node_template and PTN to draft version
        node.node_template = llm_node_template
        node.save(skip_validation=True)
        prompt_template_node.prompt_version = draft_prompt_version
        prompt_template_node.save()

        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.patch(
            url,
            {
                "prompt_template": {
                    "messages": [
                        {
                            "id": "msg-0",
                            "role": "user",
                            "content": [{"type": "text", "text": "Updated {{var}}"}],
                        }
                    ],
                    "prompt_template_id": str(prompt_template.id),
                    "prompt_version_id": str(draft_prompt_version.id),
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True

    def test_update_rejects_non_draft(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_in_active_version,
    ):
        url = _node_detail_url(graph, active_graph_version, node_in_active_version.id)
        response = authenticated_client.patch(
            url, {"name": "Should Fail"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_node_not_found(self, authenticated_client, graph, graph_version):
        fake_id = uuid.uuid4()
        url = _node_detail_url(graph, graph_version, fake_id)
        response = authenticated_client.patch(url, {"name": "Ghost"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_subgraph_node_input_mappings_e2e(
        self,
        authenticated_client,
        graph,
        graph_version,
        subgraph_node,
        node_template,
    ):
        """E2E: PATCH with input_mappings replaces existing ports/edges."""
        # Create initial input port
        initial_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="old_input",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        initial_port.save(skip_validation=True)

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

        url = _node_detail_url(graph, graph_version, subgraph_node.id)
        response = authenticated_client.patch(
            url,
            {
                "input_mappings": [
                    {"key": "context", "value": "NewSource.data"},
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify old input port deleted
        initial_port.refresh_from_db()
        assert initial_port.deleted is True

        # Verify new input port created
        new_input_ports = Port.no_workspace_objects.filter(
            node=subgraph_node, direction=PortDirection.INPUT
        )
        assert new_input_ports.count() == 1
        assert new_input_ports.first().display_name == "context"

        # Verify edge created
        edges = Edge.no_workspace_objects.filter(target_port__node=subgraph_node)
        assert edges.count() == 1
        assert edges.first().source_port == new_port

    def test_update_input_mappings_with_null_creates_edges_e2e(
        self,
        authenticated_client,
        graph,
        graph_version,
        subgraph_node,
        llm_node_template,
    ):
        """E2E: PATCH with input_mappings containing null values creates edges correctly."""
        # Create LLM source node with output port
        llm_node = Node(
            graph_version=graph_version,
            node_template=llm_node_template,
            type=NodeType.ATOMIC,
            name="llm_node_a1",
            config={},
            position={"x": 100, "y": 200},
        )
        llm_node.save(skip_validation=True)
        llm_output_port = Port(
            node=llm_node,
            key="response",
            display_name="llm_output",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
        )
        llm_output_port.save(skip_validation=True)

        # Create existing input ports on subgraph node
        Port(
            node=subgraph_node,
            key="custom",
            display_name="context",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)
        Port(
            node=subgraph_node,
            key="custom",
            display_name="question",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)

        # Create NodeConnection: llm_node_a1 → subgraph_node
        NodeConnection.no_workspace_objects.create(
            graph_version=graph_version,
            source_node=llm_node,
            target_node=subgraph_node,
        )

        # PATCH with input_mappings (user's exact request)
        url = _node_detail_url(graph, graph_version, subgraph_node.id)
        response = authenticated_client.patch(
            url,
            {
                "name": "Summary Pipeline",
                "ref_graph_version_id": str(subgraph_node.ref_graph_version_id),
                "input_mappings": [
                    {"key": "context", "value": "llm_node_a1.llm_output"},
                    {"key": "question", "value": None},
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify new input ports created (old ones replaced)
        new_input_ports = Port.no_workspace_objects.filter(
            node=subgraph_node, direction=PortDirection.INPUT
        )
        assert new_input_ports.count() == 2

        # Verify edge created from llm_node_a1.llm_output → subgraph.context
        edges = Edge.no_workspace_objects.filter(target_port__node=subgraph_node)
        assert edges.count() == 1, f"Expected 1 edge, got {edges.count()}"
        edge = edges.first()
        assert edge.source_port_id == llm_output_port.id
        assert edge.target_port.display_name == "context"

    def test_update_node_with_ports_replaces_output_only(
        self, authenticated_client, graph, graph_version, node_template
    ):
        """Test that sending ports in PATCH replaces only OUTPUT ports, preserves INPUT."""
        # Create node with initial ports
        create_url = _node_create_url(graph, graph_version)
        create_data = {
            "id": str(uuid.uuid4()),
            "type": NodeType.ATOMIC,
            "name": "Test Node",
            "node_template_id": str(node_template.id),
            "position": {"x": 100, "y": 200},
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "input1",
                    "display_name": "Input 1",
                    "direction": PortDirection.INPUT,
                    "data_schema": {"type": "string"},
                },
                {
                    "id": str(uuid.uuid4()),
                    "key": "output1",
                    "display_name": "Output 1",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "string"},
                },
            ],
        }

        create_response = authenticated_client.post(
            create_url, data=create_data, format="json"
        )
        assert (
            create_response.status_code == status.HTTP_201_CREATED
        ), f"Create failed: {create_response.data}"
        node_id = create_response.data["result"]["id"]

        # Update with new output ports only
        update_data = {
            "ports": [
                {
                    "id": str(uuid.uuid4()),
                    "key": "new_output",
                    "display_name": "New Output",
                    "direction": PortDirection.OUTPUT,
                    "data_schema": {"type": "boolean"},
                },
            ]
        }

        update_url = _node_detail_url(graph, graph_version, node_id)
        update_response = authenticated_client.patch(
            update_url, data=update_data, format="json"
        )
        assert update_response.status_code == status.HTTP_200_OK

        # Verify output port was replaced, input port preserved
        ports = update_response.data["result"]["ports"]
        assert len(ports) == 2  # 1 input (preserved) + 1 output (new)

        port_names = {p["display_name"] for p in ports}
        assert "Input 1" in port_names  # Input preserved
        assert "New Output" in port_names  # New output
        assert "Output 1" not in port_names  # Old output gone

        # Verify at database level
        node = Node.no_workspace_objects.get(id=node_id)
        active_inputs = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT, deleted=False
        )
        active_outputs = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT, deleted=False
        )

        assert active_inputs.count() == 1
        assert active_inputs.first().display_name == "Input 1"  # Preserved
        assert active_outputs.count() == 1
        assert active_outputs.first().display_name == "New Output"  # Replaced


# =====================================================================
# DELETE NODE
# =====================================================================


@pytest.mark.unit
class TestDeleteNodeAPI:
    def test_delete_node_cascade(
        self, authenticated_client, graph, graph_version, node
    ):
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["message"] == "Node deleted successfully"
        node.refresh_from_db()
        assert node.deleted is True

    def test_delete_cascades_edges(
        self,
        authenticated_client,
        graph,
        graph_version,
        node,
        output_port,
        second_node_input_port,
        edge,
    ):
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK
        edge.refresh_from_db()
        assert edge.deleted is True

    def test_delete_rejects_non_draft(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_in_active_version,
    ):
        url = _node_detail_url(graph, active_graph_version, node_in_active_version.id)
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_node_not_found(self, authenticated_client, graph, graph_version):
        fake_id = uuid.uuid4()
        url = _node_detail_url(graph, graph_version, fake_id)
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


# =====================================================================
# SYNC DATASET COLUMNS SIDE-EFFECT TESTS
# =====================================================================


SYNC_MOCK_PATH = "agent_playground.views.node.sync_dataset_columns"


@pytest.mark.unit
class TestNodeSyncSideEffects:
    """Verify sync_dataset_columns is called as a side effect of node CRUD."""

    def test_create_node_triggers_column_sync(
        self, authenticated_client, graph, graph_version, node_template
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Sync Test Node",
            "node_template_id": str(node_template.id),
        }
        with patch(SYNC_MOCK_PATH) as mock_sync:
            response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        mock_sync.assert_called_once_with(graph, graph_version)

    def test_update_node_triggers_column_sync(
        self, authenticated_client, graph, graph_version, node
    ):
        url = _node_detail_url(graph, graph_version, node.id)
        with patch(SYNC_MOCK_PATH) as mock_sync:
            response = authenticated_client.patch(
                url, {"name": "Updated"}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK
        mock_sync.assert_called_once_with(graph, graph_version)

    def test_delete_node_triggers_column_sync(
        self, authenticated_client, graph, graph_version, node
    ):
        url = _node_detail_url(graph, graph_version, node.id)
        with patch(SYNC_MOCK_PATH) as mock_sync:
            response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_200_OK
        mock_sync.assert_called_once_with(graph, graph_version)

    def test_create_node_succeeds_if_sync_fails(
        self, authenticated_client, graph, graph_version, node_template
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "Sync Fail Node",
            "node_template_id": str(node_template.id),
        }
        with patch(SYNC_MOCK_PATH, side_effect=RuntimeError("sync boom")):
            response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.unit
class TestCodeExecutionNodeAPI:
    def test_test_execution_allows_missing_workspace_context(
        self, api_client, graph, graph_version, user, organization
    ):
        api_client.force_authenticate(user=user)
        api_client.set_organization(organization)
        template = NodeTemplate.no_workspace_objects.create(
            name="code_execution",
            display_name="Code Execution",
            input_mode=PortMode.STRICT,
            output_mode=PortMode.STRICT,
            config_schema={
                "type": "object",
                "required": ["language", "code"],
                "properties": {
                    "language": {"type": "string", "enum": ["python"]},
                    "code": {"type": "string", "minLength": 1},
                },
            },
        )
        node = Node(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Code Node",
            node_template=template,
            config={"language": "python", "code": "result = inputs"},
            position={},
        )
        node.save(skip_validation=True)
        captured_context = {}

        class FakeRunner:
            def run(self, config, inputs, execution_context):
                captured_context.update(execution_context)
                return {
                    "result": {
                        "ok": True,
                        "value": inputs,
                        "stdout": "",
                        "stderr": "",
                        "exit_code": 0,
                        "duration_ms": 1,
                        "error": None,
                    }
                }

        url = _node_test_execution_url(graph, graph_version, node.id)
        with patch("agent_playground.views.node.get_runner", return_value=FakeRunner()):
            response = api_client.post(
                url,
                {
                    "config": {"language": "python", "code": "result = inputs"},
                    "inputs": {"x": 1},
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert captured_context["workspace_id"] is None
        assert response.data["result"]["result"]["value"] == {"x": 1}
        api_client.stop_workspace_injection()


# =====================================================================
# NEW BEHAVIOR TESTS — subgraph without ref, PATCH with ref,
# LLM with new prompt fields, retrieve with full expansion
# =====================================================================


@pytest.mark.unit
class TestCreateSubgraphWithoutRefAPI:
    """Subgraph nodes can now be created without ref_graph_version_id."""

    def test_create_subgraph_without_ref(
        self, authenticated_client, graph, graph_version
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "subgraph",
            "name": "Empty Sub",
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        result = response.data["result"]
        assert result["type"] == "subgraph"
        assert result["ref_graph_version_id"] is None


@pytest.mark.unit
class TestCreateLLMNodeWithNewFieldsAPI:
    """Create LLM node with tools, variable_names, metadata, etc."""

    def test_create_llm_node_with_tools_and_metadata(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "LLM Tools Node",
            "node_template_id": str(llm_node_template.id),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "Hello {{name}}"}],
                    }
                ],
                "model": "gpt-4o",
                "temperature": 0.7,
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "get_weather", "parameters": {}},
                    }
                ],
                "tool_choice": "auto",
                "variable_names": {"name": ["Alice"]},
                "metadata": {"source": "test"},
            },
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        pt = response.data["result"]["prompt_template"]
        assert pt is not None
        assert pt["prompt_template_id"] is not None
        assert pt["variable_names"] == {"name": ["Alice"]}
        assert pt["metadata"] == {"source": "test"}

    def test_create_llm_node_with_fe_output_ports(
        self, authenticated_client, graph, graph_version, llm_node_template
    ):
        """FE-supplied output ports are created alongside auto-created input ports."""
        fe_port_id = str(uuid.uuid4())
        url = _node_create_url(graph, graph_version)
        payload = {
            "id": str(uuid.uuid4()),
            "type": "atomic",
            "name": "LLM FE Ports",
            "node_template_id": str(llm_node_template.id),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{query}}"}],
                    }
                ],
            },
            "ports": [
                {
                    "id": fe_port_id,
                    "key": "response",
                    "display_name": "llm_output",
                    "direction": "output",
                    "data_schema": {"type": "string"},
                }
            ],
        }
        response = authenticated_client.post(url, payload, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        ports = response.data["result"]["ports"]
        directions = {p["direction"] for p in ports}
        assert PortDirection.INPUT in directions
        assert PortDirection.OUTPUT in directions
        output_ports = [p for p in ports if p["direction"] == PortDirection.OUTPUT]
        assert len(output_ports) == 1
        assert output_ports[0]["id"] == fe_port_id


@pytest.mark.unit
class TestUpdateNodeRefGraphVersionAPI:
    """PATCH with ref_graph_version_id."""

    def test_patch_sets_ref_graph_version(
        self,
        authenticated_client,
        graph,
        graph_version,
        active_referenced_graph_version,
    ):
        # First create a subgraph node without ref
        url = _node_create_url(graph, graph_version)
        node_id = str(uuid.uuid4())
        create_payload = {
            "id": node_id,
            "type": "subgraph",
            "name": "Sub to Update",
        }
        create_resp = authenticated_client.post(url, create_payload, format="json")
        assert create_resp.status_code == status.HTTP_201_CREATED

        # Now PATCH with ref_graph_version_id
        update_url = _node_detail_url(graph, graph_version, node_id)
        response = authenticated_client.patch(
            update_url,
            {"ref_graph_version_id": str(active_referenced_graph_version.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["ref_graph_version_id"] == str(
            active_referenced_graph_version.id
        )

    def test_patch_clears_ref_graph_version(
        self,
        authenticated_client,
        graph,
        graph_version,
        subgraph_node,
    ):
        url = _node_detail_url(graph, graph_version, subgraph_node.id)
        response = authenticated_client.patch(
            url, {"ref_graph_version_id": None}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["ref_graph_version_id"] is None

    def test_patch_with_ports_array_replaces_output_ports(
        self,
        authenticated_client,
        graph,
        graph_version,
        node,
    ):
        """PATCH with ports array replaces OUTPUT ports but preserves INPUT ports."""
        # First, create some ports on the node
        from agent_playground.models.port import Port

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

        # Send new output port via PATCH
        new_port_id = str(uuid.uuid4())
        url = _node_detail_url(graph, graph_version, node.id)
        response = authenticated_client.patch(
            url,
            {
                "ports": [
                    {
                        "id": new_port_id,
                        "key": "new_output",
                        "display_name": "New Output",
                        "direction": "output",
                        "data_schema": {"type": "boolean"},
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify input port is preserved
        port_ids = {p["id"] for p in response.data["result"]["ports"]}
        assert str(input_port.id) in port_ids

        # Verify new output port was created
        assert new_port_id in port_ids

        # Verify old output port is gone
        assert str(old_output.id) not in port_ids


@pytest.mark.unit
class TestRetrieveNodeWithNewFieldsAPI:
    """Retrieve node with full prompt_template expansion showing new fields."""

    def test_retrieve_shows_configuration_fields(
        self,
        authenticated_client,
        graph,
        graph_version,
        llm_node_template,
    ):
        # Create LLM node with full config
        create_url = _node_create_url(graph, graph_version)
        node_id = str(uuid.uuid4())
        payload = {
            "id": node_id,
            "type": "atomic",
            "name": "Full LLM",
            "node_template_id": str(llm_node_template.id),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{input}}"}],
                    }
                ],
                "model": "gpt-4o",
                "temperature": 0.5,
                "output_format": "markdown",
                "model_detail": {"provider": "openai"},
            },
        }
        create_resp = authenticated_client.post(create_url, payload, format="json")
        assert create_resp.status_code == status.HTTP_201_CREATED

        # Retrieve and verify
        retrieve_url = _node_detail_url(graph, graph_version, node_id)
        response = authenticated_client.get(retrieve_url)

        assert response.status_code == status.HTTP_200_OK
        pt = response.data["result"]["prompt_template"]
        assert pt["model"] == "gpt-4o"
        assert pt["temperature"] == 0.5
        assert pt["output_format"] == "markdown"
        assert pt["model_detail"]["provider"] == "openai"
        assert pt["is_draft"] is True
        assert pt["template_version"] is not None

    def test_retrieve_shows_variable_names_and_metadata(
        self,
        authenticated_client,
        graph,
        graph_version,
        llm_node_template,
    ):
        create_url = _node_create_url(graph, graph_version)
        node_id = str(uuid.uuid4())
        payload = {
            "id": node_id,
            "type": "atomic",
            "name": "Vars LLM",
            "node_template_id": str(llm_node_template.id),
            "prompt_template": {
                "messages": [
                    {
                        "id": "msg-0",
                        "role": "user",
                        "content": [{"type": "text", "text": "{{x}} and {{y}}"}],
                    }
                ],
                "variable_names": {"x": ["a"], "y": ["b"]},
                "metadata": {"source": "playground"},
            },
        }
        create_resp = authenticated_client.post(create_url, payload, format="json")
        assert create_resp.status_code == status.HTTP_201_CREATED

        retrieve_url = _node_detail_url(graph, graph_version, node_id)
        response = authenticated_client.get(retrieve_url)

        assert response.status_code == status.HTTP_200_OK
        pt = response.data["result"]["prompt_template"]
        assert pt["variable_names"] == {"x": ["a"], "y": ["b"]}
        assert pt["metadata"] == {"source": "playground"}


# =====================================================================
# POSSIBLE EDGE MAPPINGS
# =====================================================================


@pytest.mark.unit
class TestNodePossibleEdgeMappings:
    """Tests for GET /nodes/{node_id}/possible-edge-mappings/ endpoint."""

    def test_returns_source_nodes_with_output_ports(
        self, authenticated_client, graph, graph_version, node_template
    ):
        """Test returns all source nodes and their output ports."""
        # Create nodes: A → C ← B
        node_a = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node A",
            node_template=node_template,
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node B",
            node_template=node_template,
        )
        node_c = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node C",
            node_template=node_template,
        )

        # Create output ports
        Port.no_workspace_objects.create(
            node=node_a,
            key="output1",
            display_name="result",
            direction=PortDirection.OUTPUT,
        )
        Port.no_workspace_objects.create(
            node=node_b,
            key="output1",
            display_name="data",
            direction=PortDirection.OUTPUT,
        )

        # Create NodeConnections: A → C, B → C
        nc1 = NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node_a, target_node=node_c
        )
        nc2 = NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node_b, target_node=node_c
        )

        url = reverse(
            "node-possible-edge-mappings",
            kwargs={
                "pk": graph.id,
                "version_id": graph_version.id,
                "node_id": node_c.id,
            },
        )

        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True

        result = response.data["result"]
        assert len(result) == 2

        # Check Node A data
        node_a_data = next(r for r in result if r["source_node_id"] == str(node_a.id))
        assert node_a_data["source_node_name"] == "Node A"
        assert node_a_data["node_connection_id"] == str(nc1.id)
        assert len(node_a_data["output_ports"]) == 1
        assert node_a_data["output_ports"][0]["display_name"] == "result"

        # Check Node B data
        node_b_data = next(r for r in result if r["source_node_id"] == str(node_b.id))
        assert node_b_data["source_node_name"] == "Node B"
        assert node_b_data["node_connection_id"] == str(nc2.id)
        assert len(node_b_data["output_ports"]) == 1
        assert node_b_data["output_ports"][0]["display_name"] == "data"

    def test_returns_empty_for_no_incoming_connections(
        self, authenticated_client, graph, graph_version, node_template
    ):
        """Test returns empty array when node has no incoming connections."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Isolated Node",
            node_template=node_template,
        )

        url = reverse(
            "node-possible-edge-mappings",
            kwargs={"pk": graph.id, "version_id": graph_version.id, "node_id": node.id},
        )

        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"] == []

    def test_excludes_soft_deleted_connections(
        self, authenticated_client, graph, graph_version, node_template
    ):
        """Test excludes soft-deleted NodeConnections."""
        node_a = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node A",
            node_template=node_template,
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=graph_version,
            type=NodeType.ATOMIC,
            name="Node B",
            node_template=node_template,
        )

        Port.no_workspace_objects.create(
            node=node_a,
            key="output1",
            display_name="result",
            direction=PortDirection.OUTPUT,
        )

        # Create connection then soft-delete it
        nc = NodeConnection.no_workspace_objects.create(
            graph_version=graph_version, source_node=node_a, target_node=node_b
        )
        nc.deleted = True
        nc.save()

        url = reverse(
            "node-possible-edge-mappings",
            kwargs={
                "pk": graph.id,
                "version_id": graph_version.id,
                "node_id": node_b.id,
            },
        )

        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"] == []

    def test_returns_404_for_nonexistent_node(
        self, authenticated_client, graph, graph_version
    ):
        """Test returns 404 when node doesn't exist."""
        url = reverse(
            "node-possible-edge-mappings",
            kwargs={
                "pk": graph.id,
                "version_id": graph_version.id,
                "node_id": uuid.uuid4(),
            },
        )

        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

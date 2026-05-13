"""Tests for GraphExecutionViewSet."""

import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
    NodeExecutionStatus,
    NodeType,
    PortDirection,
)
from agent_playground.models.execution_data import ExecutionData
from agent_playground.models.graph import Graph
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_execution import NodeExecution
from agent_playground.models.port import Port

# =============================================================================
# GraphExecution list
# =============================================================================


@pytest.mark.unit
class TestGraphExecutionList:
    """Tests for GET /graphs/<graph_id>/executions/."""

    def test_success_returns_paginated_executions(
        self, authenticated_client, graph, active_graph_version
    ):
        """Create 2 executions, GET → 200, executions has 2 items, metadata present."""
        GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.FAILED,
        )

        url = reverse("graph-execution-list", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert len(result["executions"]) == 2
        assert "metadata" in result
        assert result["metadata"]["total_count"] == 2

    def test_empty_list(self, authenticated_client, graph):
        """Graph with no executions returns 200 with empty list."""
        url = reverse("graph-execution-list", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["executions"] == []
        assert result["metadata"]["total_count"] == 0

    def test_pagination_metadata(
        self, authenticated_client, graph, active_graph_version
    ):
        """Pagination metadata is correct."""
        for _ in range(3):
            GraphExecution.no_workspace_objects.create(
                graph_version=active_graph_version,
                status=GraphExecutionStatus.PENDING,
            )

        url = reverse("graph-execution-list", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url, {"page_size": 2, "page_number": 1})

        assert response.status_code == status.HTTP_200_OK
        metadata = response.data["result"]["metadata"]
        assert metadata["total_count"] == 3
        assert metadata["total_pages"] == 2
        assert metadata["next_page"] == 2
        assert len(response.data["result"]["executions"]) == 2

    def test_ordered_by_newest_first(
        self, authenticated_client, graph, active_graph_version
    ):
        """Results are ordered by created_at descending."""
        older = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        newer = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.FAILED,
        )

        url = reverse("graph-execution-list", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        executions = response.data["result"]["executions"]
        assert executions[0]["id"] == str(newer.id)
        assert executions[1]["id"] == str(older.id)

    def test_child_executions_excluded_from_list(
        self, authenticated_client, graph, active_graph_version, node_template
    ):
        """Child (subgraph) executions should not appear in the list."""
        top_exec = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        # Create a node and its execution
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Subgraph Node",
            config={},
            position={"x": 0, "y": 0},
        )
        node_exec = NodeExecution.no_workspace_objects.create(
            graph_execution=top_exec,
            node=node,
            status=NodeExecutionStatus.SUCCESS,
        )
        # Create a child execution spawned by the subgraph node
        GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            parent_node_execution=node_exec,
            status=GraphExecutionStatus.SUCCESS,
        )

        url = reverse("graph-execution-list", kwargs={"graph_id": graph.id})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        executions = response.data["result"]["executions"]
        assert len(executions) == 1
        assert executions[0]["id"] == str(top_exec.id)

    def test_unauthenticated(self, api_client, graph):
        """Unauthenticated request returns 401 or 403."""
        url = reverse("graph-execution-list", kwargs={"graph_id": graph.id})
        response = api_client.get(url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# =============================================================================
# GraphExecution retrieve
# =============================================================================


@pytest.mark.unit
class TestGraphExecutionRetrieve:
    """Tests for GET /graphs/<graph_id>/executions/<execution_id>/."""

    def test_success(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_template,
    ):
        """Retrieve returns execution detail with nodes and edges."""
        # Create a node with ports on the active version
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Exec Node",
            config={},
            position={"x": 0, "y": 0},
        )
        Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
            required=True,
        )

        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
            input_payload={"key": "value"},
        )
        NodeExecution.no_workspace_objects.create(
            graph_execution=execution,
            node=node,
            status=NodeExecutionStatus.SUCCESS,
        )

        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert "nodes" in result
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["node_execution"] is not None

    def test_non_subgraph_node_has_null_sub_graph(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_template,
    ):
        """Atomic nodes have sub_graph=None."""
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Atomic Node",
            config={},
            position={"x": 0, "y": 0},
        )
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        NodeExecution.no_workspace_objects.create(
            graph_execution=execution,
            node=node,
            status=NodeExecutionStatus.SUCCESS,
        )

        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["nodes"][0]["sub_graph"] is None

    def test_not_found_random_execution_id(self, authenticated_client, graph):
        """404 when execution_id doesn't exist."""
        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": graph.id, "execution_id": uuid.uuid4()},
        )
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_not_found_graph_mismatch(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        organization,
        workspace,
        user,
    ):
        """404 when graph_id doesn't match the execution's graph."""
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.PENDING,
        )
        from agent_playground.models.graph import Graph

        other_graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Other Graph",
            created_by=user,
        )

        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": other_graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated(self, api_client, graph):
        """403 when not authenticated."""
        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": graph.id, "execution_id": uuid.uuid4()},
        )
        response = api_client.get(url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_graph_execution_node_connections_empty_when_no_connections(
        self, authenticated_client, graph, active_graph_version, node_template, user
    ):
        """Graph execution returns empty node_connections at the top level when none exist."""
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Isolated Node",
            config={},
            position={"x": 0, "y": 0},
        )

        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
            input_payload={"test": "data"},
            output_payload={"result": "success"},
        )

        NodeExecution.no_workspace_objects.create(
            graph_execution=execution,
            node=node,
            status=NodeExecutionStatus.SUCCESS,
        )

        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert "node_connections" in result
        assert result["node_connections"] == []

    def test_graph_execution_node_connections_at_top_level(
        self, authenticated_client, graph, active_graph_version, node_template, user
    ):
        """Graph execution returns node_connections at the top level of the response."""
        # Create three nodes: A -> B -> C
        node_a = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node A",
            config={},
            position={"x": 0, "y": 0},
        )
        node_b = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node B",
            config={},
            position={"x": 100, "y": 0},
        )
        node_c = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Node C",
            config={},
            position={"x": 200, "y": 0},
        )

        # Create connections: A -> B -> C
        nc_ab = NodeConnection.no_workspace_objects.create(
            graph_version=active_graph_version,
            source_node=node_a,
            target_node=node_b,
        )
        nc_bc = NodeConnection.no_workspace_objects.create(
            graph_version=active_graph_version,
            source_node=node_b,
            target_node=node_c,
        )

        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
            input_payload={"test": "data"},
        )

        for node in [node_a, node_b, node_c]:
            NodeExecution.no_workspace_objects.create(
                graph_execution=execution,
                node=node,
                status=NodeExecutionStatus.SUCCESS,
            )

        url = reverse(
            "graph-execution-detail",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]

        # node_connections should be at the top level, not inside nodes
        assert "node_connections" in result
        assert len(result["node_connections"]) == 2

        connections_by_id = {str(nc["id"]): nc for nc in result["node_connections"]}

        # Verify A -> B connection
        ab = connections_by_id[str(nc_ab.id)]
        assert str(ab["source_node_id"]) == str(node_a.id)
        assert str(ab["target_node_id"]) == str(node_b.id)

        # Verify B -> C connection
        bc = connections_by_id[str(nc_bc.id)]
        assert str(bc["source_node_id"]) == str(node_b.id)
        assert str(bc["target_node_id"]) == str(node_c.id)

        # Verify nodes do NOT have node_connections
        for node_data in result["nodes"]:
            assert "node_connections" not in node_data


# =============================================================================
# Graph execution evaluation
# =============================================================================


@pytest.mark.unit
class TestGraphExecutionEvaluate:
    """Tests for POST /graphs/<graph_id>/executions/<execution_id>/evaluate/."""

    def test_runs_agent_level_eval_against_mapped_node_outputs(
        self,
        authenticated_client,
        graph,
        active_graph_version,
        node_template,
        monkeypatch,
    ):
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Answer Node",
            config={},
            position={"x": 0, "y": 0},
        )
        output_port = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="answer",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
            required=True,
        )
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
            output_payload={},
        )
        node_execution = NodeExecution.no_workspace_objects.create(
            graph_execution=execution,
            node=node,
            status=NodeExecutionStatus.SUCCESS,
        )
        ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=output_port,
            payload="Paris",
        )

        observed = {}

        def fake_run_agent_evaluation_batch(
            evaluators,
            graph_execution,
            execution_context,
            threshold,
            fallback_mappings=None,
        ):
            observed.update(
                {
                    "evaluators": evaluators,
                    "graph_execution": graph_execution,
                    "threshold": threshold,
                    "execution_context": execution_context,
                    "fallback_mappings": fallback_mappings,
                }
            )
            return {
                "score": 0.91,
                "passed": True,
                "threshold": threshold,
                "results": [{"name": "accuracy", "score": 0.91, "passed": True}],
            }

        monkeypatch.setattr(
            "agent_playground.views.graph_execution.run_agent_evaluation_batch",
            fake_run_agent_evaluation_batch,
        )

        url = reverse(
            "graph-execution-evaluate",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.post(
            url,
            {
                "evaluators": [{"templateId": str(uuid.uuid4()), "name": "accuracy"}],
                "threshold": 0.8,
                "mappings": {"output": f"{node.id}.output1"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert result["score"] == 0.91
        assert result["passed"] is True
        assert result["history"][0]["score"] == 0.91
        assert observed["graph_execution"] == execution
        assert observed["fallback_mappings"] == {"output": f"{node.id}.output1"}
        assert observed["threshold"] == 0.8

        execution.refresh_from_db()
        assert execution.output_payload["agent_evaluations"][0]["passed"] is True

    def test_returns_400_for_missing_mapped_output(
        self,
        authenticated_client,
        graph,
        active_graph_version,
    ):
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        url = reverse(
            "graph-execution-evaluate",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.post(
            url,
            {
                "evaluators": [{"templateId": str(uuid.uuid4())}],
                "mappings": {"output": f"{uuid.uuid4()}.answer"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Missing execution outputs" in response.data["result"]

    def test_returns_400_for_incomplete_execution(
        self,
        authenticated_client,
        graph,
        active_graph_version,
    ):
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.RUNNING,
        )
        url = reverse(
            "graph-execution-evaluate",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.post(
            url,
            {
                "evaluators": [{"templateId": str(uuid.uuid4())}],
                "mappings": {"output": f"{uuid.uuid4()}.answer"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "successful execution" in response.data["result"]

    def test_returns_400_for_null_threshold(
        self,
        authenticated_client,
        graph,
        active_graph_version,
    ):
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        url = reverse(
            "graph-execution-evaluate",
            kwargs={"graph_id": graph.id, "execution_id": execution.id},
        )
        response = authenticated_client.post(
            url,
            {
                "evaluators": [{"templateId": str(uuid.uuid4())}],
                "threshold": None,
                "mappings": {"output": f"{uuid.uuid4()}.answer"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "threshold" in response.data["result"]


# =============================================================================
# Node execution detail
# =============================================================================


@pytest.mark.unit
class TestNodeExecutionDetail:
    """Tests for GET /executions/<execution_id>/nodes/<node_execution_id>/."""

    def _create_execution_with_data(self, active_graph_version, node_template):
        """Helper to create an execution with input/output execution data."""
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Detail Node",
            config={},
            position={"x": 0, "y": 0},
        )
        input_port = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
            required=True,
        )
        output_port = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
            required=True,
        )

        now = timezone.now()
        graph_execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        node_execution = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node,
            status=NodeExecutionStatus.SUCCESS,
            started_at=now - timedelta(seconds=5),
            completed_at=now,
        )
        ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=input_port,
            payload="input value",
        )
        ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=output_port,
            payload="output value",
        )
        return graph_execution, node_execution

    def test_success_with_inputs_and_outputs(
        self, authenticated_client, active_graph_version, node_template
    ):
        """Returns inputs and outputs grouped by port direction."""
        graph_exec, node_exec = self._create_execution_with_data(
            active_graph_version, node_template
        )

        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": graph_exec.id,
                "node_execution_id": node_exec.id,
            },
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert len(result["inputs"]) == 1
        assert len(result["outputs"]) == 1
        assert result["inputs"][0]["port_direction"] == "input"
        assert result["outputs"][0]["port_direction"] == "output"

    def test_duration_calculated(
        self, authenticated_client, active_graph_version, node_template
    ):
        """Duration is computed when both timestamps are present."""
        graph_exec, node_exec = self._create_execution_with_data(
            active_graph_version, node_template
        )

        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": graph_exec.id,
                "node_execution_id": node_exec.id,
            },
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        duration = response.data["result"]["duration_seconds"]
        assert duration is not None
        assert duration == pytest.approx(5.0, abs=0.1)

    def test_duration_none_without_timestamps(
        self, authenticated_client, active_graph_version, node_template
    ):
        """Duration is None when timestamps are missing."""
        node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="No-ts Node",
            config={},
            position={"x": 0, "y": 0},
        )
        graph_execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.RUNNING,
        )
        node_execution = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node,
            status=NodeExecutionStatus.PENDING,
        )

        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": graph_execution.id,
                "node_execution_id": node_execution.id,
            },
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["duration_seconds"] is None

    def test_not_found_execution(self, authenticated_client):
        """404 when execution doesn't exist."""
        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": uuid.uuid4(),
                "node_execution_id": uuid.uuid4(),
            },
        )
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_not_found_node_execution(self, authenticated_client, active_graph_version):
        """404 when node_execution doesn't exist for the given execution."""
        graph_execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )

        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": graph_execution.id,
                "node_execution_id": uuid.uuid4(),
            },
        )
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_success_for_child_execution(
        self, authenticated_client, active_graph_version, node_template
    ):
        """node_detail returns 200 when execution_id is a child (subgraph) execution."""
        # Create a separate graph + active version for the subgraph reference
        ref_graph = Graph.no_workspace_objects.create(
            organization=active_graph_version.graph.organization,
            workspace=active_graph_version.graph.workspace,
            name="Referenced Graph",
            created_by=active_graph_version.graph.created_by,
        )
        ref_graph_version = GraphVersion.no_workspace_objects.create(
            graph=ref_graph,
            version_number=1,
            status=GraphVersionStatus.ACTIVE,
            tags=[],
        )

        # Create a top-level execution and a node execution that spawns a subgraph
        top_exec = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            status=GraphExecutionStatus.SUCCESS,
        )
        parent_node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            ref_graph_version=ref_graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Node",
            config={},
            position={"x": 0, "y": 0},
        )
        parent_node_exec = NodeExecution.no_workspace_objects.create(
            graph_execution=top_exec,
            node=parent_node,
            status=NodeExecutionStatus.SUCCESS,
        )

        # Create the child execution (has parent_node_execution set)
        child_exec = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            parent_node_execution=parent_node_exec,
            status=GraphExecutionStatus.SUCCESS,
        )

        # Create a node and node execution inside the child execution
        child_node = Node.no_workspace_objects.create(
            graph_version=active_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Inner Node",
            config={},
            position={"x": 0, "y": 0},
        )
        child_node_exec = NodeExecution.no_workspace_objects.create(
            graph_execution=child_exec,
            node=child_node,
            status=NodeExecutionStatus.SUCCESS,
        )

        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": child_exec.id,
                "node_execution_id": child_node_exec.id,
            },
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        result = response.data["result"]
        assert str(result["node_execution_id"]) == str(child_node_exec.id)
        assert result["node_name"] == "Inner Node"

    def test_unauthenticated(self, api_client):
        """403/401 when not authenticated."""
        url = reverse(
            "graph-node-execution-detail",
            kwargs={
                "execution_id": uuid.uuid4(),
                "node_execution_id": uuid.uuid4(),
            },
        )
        response = api_client.get(url)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

"""
Tests for the NodeExecution model.
"""

import pytest
from django.db import IntegrityError, models

from agent_playground.models import GraphExecution, Node, NodeExecution
from agent_playground.models.choices import NodeExecutionStatus


@pytest.mark.unit
class TestNodeExecutionCreation:
    """Tests for NodeExecution model creation."""

    def test_node_execution_creation_success(
        self, db, graph_execution, node_in_active_version
    ):
        """Basic creation."""
        execution = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node_in_active_version,
        )
        assert execution.id is not None
        assert execution.graph_execution == graph_execution
        assert execution.node == node_in_active_version

    def test_node_execution_default_status_pending(
        self, db, graph_execution, node_in_active_version
    ):
        """status defaults to PENDING."""
        execution = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node_in_active_version,
        )
        assert execution.status == NodeExecutionStatus.PENDING


@pytest.mark.unit
class TestNodeExecutionStatusValidation:
    """Tests for NodeExecution status validation."""

    def test_node_execution_all_statuses_valid(
        self, db, graph_execution, node_in_active_version
    ):
        """PENDING, RUNNING, SUCCESS, FAILED, SKIPPED are all valid."""
        statuses = [
            NodeExecutionStatus.PENDING,
            NodeExecutionStatus.RUNNING,
            NodeExecutionStatus.SUCCESS,
            NodeExecutionStatus.FAILED,
            NodeExecutionStatus.SKIPPED,
        ]
        for idx, status in enumerate(statuses):
            # Need to create a new node for each to avoid unique constraint
            node = Node.no_workspace_objects.create(
                graph_version=node_in_active_version.graph_version,
                node_template=node_in_active_version.node_template,
                type=node_in_active_version.type,
                name=f"Node {idx}",
                config={},
            )
            execution = NodeExecution.no_workspace_objects.create(
                graph_execution=graph_execution,
                node=node,
                status=status,
            )
            execution.full_clean()
            assert execution.status == status


@pytest.mark.unit
class TestNodeExecutionUniqueConstraint:
    """Tests for NodeExecution unique constraints."""

    def test_node_execution_unique_per_graph_execution(
        self, db, graph_execution, node_in_active_version
    ):
        """UniqueConstraint on (graph_execution, node)."""
        NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node_in_active_version,
        )

        with pytest.raises(IntegrityError):
            NodeExecution.no_workspace_objects.create(
                graph_execution=graph_execution,
                node=node_in_active_version,  # Duplicate
            )

    def test_node_execution_unique_allows_deleted(
        self, db, graph_execution, node_in_active_version
    ):
        """Soft-deleted node executions don't block uniqueness."""
        execution1 = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node_in_active_version,
        )
        execution1.delete()  # Soft delete

        # Should be able to create another execution for same node
        execution2 = NodeExecution.no_workspace_objects.create(
            graph_execution=graph_execution,
            node=node_in_active_version,
        )
        assert execution2.id is not None

    def test_node_execution_same_node_different_graph_executions(
        self, db, active_graph_version, node_in_active_version
    ):
        """Same node can have executions in different graph executions."""
        exec1 = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version
        )
        exec2 = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version
        )

        node_exec1 = NodeExecution.no_workspace_objects.create(
            graph_execution=exec1,
            node=node_in_active_version,
        )
        node_exec2 = NodeExecution.no_workspace_objects.create(
            graph_execution=exec2,
            node=node_in_active_version,
        )
        assert node_exec1.node == node_exec2.node
        assert node_exec1.graph_execution != node_exec2.graph_execution


@pytest.mark.unit
class TestNodeExecutionCascadeDelete:
    """Tests for NodeExecution cascade delete behavior."""

    def test_node_execution_cascade_delete_graph_execution(
        self, db, node_execution, graph_execution
    ):
        """Deleting graph_execution cascades to node executions."""
        node_exec_id = node_execution.id

        # Hard delete the graph execution
        GraphExecution.all_objects.filter(id=graph_execution.id).delete()

        # Node execution should be gone
        assert not NodeExecution.all_objects.filter(id=node_exec_id).exists()


@pytest.mark.unit
class TestNodeExecutionProtectDelete:
    """Tests for NodeExecution PROTECT delete behavior."""

    def test_node_execution_protect_delete_node(
        self, db, node_execution, node_in_active_version
    ):
        """Cannot delete node with executions (PROTECT)."""
        with pytest.raises((IntegrityError, models.ProtectedError)):
            Node.all_objects.filter(id=node_in_active_version.id).delete()

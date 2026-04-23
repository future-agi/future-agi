"""
Tests for the GraphExecution model.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models

from agent_playground.models import GraphExecution, GraphVersion, NodeExecution
from agent_playground.models.choices import (
    GraphExecutionStatus,
    NodeExecutionStatus,
)


@pytest.mark.unit
class TestGraphExecutionCreation:
    """Tests for GraphExecution model creation."""

    def test_graph_execution_creation_success(self, db, active_graph_version):
        """Basic creation."""
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            input_payload={"test": "data"},
        )
        assert execution.id is not None
        assert execution.graph_version == active_graph_version

    def test_graph_execution_default_status_pending(self, db, active_graph_version):
        """status defaults to PENDING."""
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
        )
        assert execution.status == GraphExecutionStatus.PENDING


@pytest.mark.unit
class TestGraphExecutionStatusValidation:
    """Tests for GraphExecution status validation."""

    def test_graph_execution_all_statuses_valid(self, db, active_graph_version):
        """PENDING, RUNNING, SUCCESS, FAILED, CANCELLED are all valid."""
        statuses = [
            GraphExecutionStatus.PENDING,
            GraphExecutionStatus.RUNNING,
            GraphExecutionStatus.SUCCESS,
            GraphExecutionStatus.FAILED,
            GraphExecutionStatus.CANCELLED,
        ]
        for status in statuses:
            execution = GraphExecution.no_workspace_objects.create(
                graph_version=active_graph_version,
                status=status,
            )
            execution.full_clean()
            assert execution.status == status


@pytest.mark.unit
class TestGraphExecutionPayloadValidation:
    """Tests for GraphExecution payload validation."""

    def test_graph_execution_input_payload_must_be_dict(self, db, active_graph_version):
        """clean() validates input_payload is dict type."""
        execution = GraphExecution(
            graph_version=active_graph_version,
            input_payload="not_a_dict",  # Invalid
        )
        with pytest.raises(ValidationError, match="input_payload must be a dict"):
            execution.clean()

    def test_graph_execution_output_payload_must_be_dict(
        self, db, active_graph_version
    ):
        """clean() validates output_payload is dict type."""
        execution = GraphExecution(
            graph_version=active_graph_version,
            output_payload="not_a_dict",  # Invalid
        )
        with pytest.raises(ValidationError, match="output_payload must be a dict"):
            execution.clean()

    def test_graph_execution_payload_allows_none(self, db, active_graph_version):
        """None is valid for payloads."""
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            input_payload=None,
            output_payload=None,
        )
        execution.clean()  # Should not raise
        assert execution.input_payload is None
        assert execution.output_payload is None

    def test_graph_execution_payload_allows_empty_dict(self, db, active_graph_version):
        """Empty dict is valid for payloads."""
        execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            input_payload={},
            output_payload={},
        )
        execution.clean()
        assert execution.input_payload == {}
        assert execution.output_payload == {}


@pytest.mark.unit
class TestGraphExecutionDeleteBehavior:
    """Tests for GraphExecution delete behavior."""

    def test_graph_execution_protect_delete_graph_version(
        self, db, active_graph_version
    ):
        """Cannot delete version with executions (PROTECT)."""
        GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
        )

        with pytest.raises((IntegrityError, models.ProtectedError)):
            GraphVersion.all_objects.filter(id=active_graph_version.id).delete()

    def test_graph_execution_cascade_delete_parent(
        self, db, active_graph_version, node_in_active_version
    ):
        """Deleting parent_node_execution cascades to child graph executions."""
        # Create a parent graph execution and node execution
        parent_execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
        )
        parent_node_execution = NodeExecution.no_workspace_objects.create(
            graph_execution=parent_execution,
            node=node_in_active_version,
            status=NodeExecutionStatus.RUNNING,
        )

        # Create child graph execution
        child_execution = GraphExecution.no_workspace_objects.create(
            graph_version=active_graph_version,
            parent_node_execution=parent_node_execution,
        )
        child_id = child_execution.id

        # Hard delete the parent node execution
        NodeExecution.all_objects.filter(id=parent_node_execution.id).delete()

        # Child execution should be gone
        assert not GraphExecution.all_objects.filter(id=child_id).exists()

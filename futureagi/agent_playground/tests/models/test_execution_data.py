"""
Tests for the ExecutionData model.
"""

import pytest
from django.db import IntegrityError, models

from agent_playground.models import ExecutionData, Node, NodeExecution, Port
from agent_playground.models.choices import PortDirection


@pytest.mark.unit
class TestExecutionDataCreation:
    """Tests for ExecutionData model creation."""

    def test_execution_data_creation_success(self, db, node_execution):
        """Basic creation."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="test value",
        )
        assert data.id is not None
        assert data.node_execution == node_execution
        assert data.port == port


@pytest.mark.unit
class TestExecutionDataUniqueConstraint:
    """Tests for ExecutionData unique constraints."""

    def test_execution_data_unique_per_port(self, db, node_execution):
        """UniqueConstraint on (node_execution, port)."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="first",
        )

        with pytest.raises(IntegrityError):
            ExecutionData.no_workspace_objects.create(
                node_execution=node_execution,
                port=port,  # Duplicate
                payload="second",
            )

    def test_execution_data_unique_allows_deleted(self, db, node_execution):
        """Soft-deleted execution data don't block uniqueness."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        data1 = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="first",
        )
        data1.delete()  # Soft delete

        # Should be able to create another data for same port
        data2 = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="second",
        )
        assert data2.id is not None


@pytest.mark.unit
class TestExecutionDataPayloadValidation:
    """Tests for ExecutionData payload validation."""

    def test_execution_data_auto_validates_payload(self, db, node_execution):
        """save() runs validate_payload()."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="valid string",
        )
        assert data.is_valid is True
        assert data.validation_errors is None

    def test_execution_data_valid_payload_is_valid_true(self, db, node_execution):
        """Matching schema sets is_valid=True."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="a valid string",
        )
        assert data.is_valid is True

    def test_execution_data_invalid_payload_is_valid_false(self, db, node_execution):
        """Non-matching schema sets is_valid=False."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "integer"},
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="not an integer",  # Invalid
        )
        assert data.is_valid is False

    def test_execution_data_validation_errors_populated(self, db, node_execution):
        """validation_errors contains details for invalid data."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={"type": "number"},
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="not a number",
        )
        assert data.is_valid is False
        assert data.validation_errors is not None
        assert "message" in data.validation_errors

    def test_execution_data_empty_schema_always_valid(self, db, node_execution):
        """Empty data_schema = always valid."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={},  # Empty schema
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload={"anything": "goes", "even": [1, 2, 3]},
        )
        assert data.is_valid is True
        assert data.validation_errors is None

    def test_execution_data_complex_schema_validation(self, db, node_execution):
        """Complex schema validation works correctly."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name"],
            },
        )
        # Valid payload
        valid_data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload={"name": "John", "age": 30},
        )
        assert valid_data.is_valid is True

    def test_execution_data_complex_schema_missing_required(self, db, node_execution):
        """Missing required field fails validation."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            data_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        )
        # Invalid - missing required field
        invalid_data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload={"other": "field"},
        )
        assert invalid_data.is_valid is False


@pytest.mark.unit
class TestExecutionDataDenormalization:
    """Tests for ExecutionData node denormalization."""

    def test_execution_data_denormalizes_node(self, db, node_execution):
        """save() sets node from node_execution.node."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="test",
        )
        assert data.node == node_execution.node

    def test_execution_data_node_not_overwritten_if_set(self, db, node_execution):
        """If node_id is already set, it's not overwritten."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        data = ExecutionData(
            node_execution=node_execution,
            node=node_execution.node,  # Explicitly set
            port=port,
            payload="test",
        )
        data.save()
        assert data.node == node_execution.node


@pytest.mark.unit
class TestExecutionDataCascadeDelete:
    """Tests for ExecutionData cascade delete behavior."""

    def test_execution_data_cascade_delete_node_execution(self, db, node_execution):
        """Deleting node_execution cascades to execution data."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        data = ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="test",
        )
        data_id = data.id

        # Hard delete the node execution
        NodeExecution.all_objects.filter(id=node_execution.id).delete()

        # Execution data should be gone
        assert not ExecutionData.all_objects.filter(id=data_id).exists()


@pytest.mark.unit
class TestExecutionDataProtectDelete:
    """Tests for ExecutionData PROTECT delete behavior."""

    def test_execution_data_protect_delete_node(self, db, node_execution):
        """Cannot delete node with execution data (PROTECT)."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="test",
        )

        with pytest.raises((IntegrityError, models.ProtectedError)):
            Node.all_objects.filter(id=node_execution.node.id).delete()

    def test_execution_data_protect_delete_port(self, db, node_execution):
        """Cannot delete port with execution data (PROTECT)."""
        port = Port.no_workspace_objects.create(
            node=node_execution.node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )
        ExecutionData.no_workspace_objects.create(
            node_execution=node_execution,
            port=port,
            payload="test",
        )

        with pytest.raises((IntegrityError, models.ProtectedError)):
            Port.all_objects.filter(id=port.id).delete()

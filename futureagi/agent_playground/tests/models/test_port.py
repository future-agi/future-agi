"""
Tests for the Port model.

Note: The `node` fixture uses a STRICT-mode template with keys 'input1' and 'output1'.
Tests that need arbitrary keys use the `dynamic_node` fixture (DYNAMIC-mode template).
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from agent_playground.models import Node, Port
from agent_playground.models.choices import NodeType, PortDirection


@pytest.mark.unit
class TestPortCreation:
    """Tests for Port model creation."""

    def test_port_creation_success(self, db, node):
        """Basic port creation."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        )
        assert port.id is not None
        assert port.key == "input1"
        assert port.node == node

    def test_port_direction_input(self, db, node):
        """direction=INPUT valid."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
            metadata={"label": "Input"},
        )
        port.full_clean()
        assert port.direction == PortDirection.INPUT

    def test_port_direction_output(self, db, node):
        """direction=OUTPUT valid."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="Output 1",
            direction=PortDirection.OUTPUT,
            data_schema={"type": "string"},
            metadata={"label": "Output"},
        )
        port.full_clean()
        assert port.direction == PortDirection.OUTPUT


@pytest.mark.unit
class TestPortUniqueConstraint:
    """Tests for Port unique constraints."""

    def test_port_unique_key_per_node(self, db, node):
        """UniqueConstraint on (node, key)."""
        Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )

        with pytest.raises(IntegrityError):
            Port.no_workspace_objects.create(
                node=node,
                key="input1",  # Duplicate key
                display_name="Input 1 Copy",
                direction=PortDirection.INPUT,  # Same direction, same key
            )

    def test_port_unique_allows_deleted(self, db, node):
        """Soft-deleted ports don't block uniqueness."""
        port1 = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )
        port1.delete()  # Soft delete

        # Should be able to create another port with same key
        port2 = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )
        assert port2.id is not None
        assert port2.key == "input1"

    def test_port_same_key_different_nodes(self, db, node, second_node):
        """Same key allowed on different nodes."""
        port1 = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )
        port2 = Port.no_workspace_objects.create(
            node=second_node,
            key="input1",  # Same key, different node
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )
        assert port1.key == port2.key
        assert port1.node != port2.node


@pytest.mark.unit
class TestPortCascadeDelete:
    """Tests for Port cascade delete behavior."""

    def test_port_cascade_delete_node(self, db, node):
        """Deleting node cascades to ports."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )
        port_id = port.id

        # Hard delete the node
        Node.all_objects.filter(id=node.id).delete()

        # Port should be gone
        assert not Port.all_objects.filter(id=port_id).exists()


@pytest.mark.unit
class TestPortDefaultValues:
    """Tests for Port default values."""

    def test_port_default_values(self, db, node):
        """required=True, data_schema={}, metadata={}."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Input 1",
            direction=PortDirection.INPUT,
        )
        assert port.required is True
        assert port.data_schema == {}
        assert port.metadata == {}
        assert port.default_value is None


@pytest.mark.unit
class TestPortDisplayName:
    """Tests for Port display_name field."""

    def test_display_name_explicit(self, db, node):
        """display_name can be set explicitly."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="LLM Response",
            direction=PortDirection.OUTPUT,
        )
        assert port.display_name == "LLM Response"
        assert port.key == "output1"

    def test_display_name_unique_per_node(self, db, node):
        """UniqueConstraint on (node, display_name)."""
        Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Same Name",
            direction=PortDirection.INPUT,
        )
        with pytest.raises(IntegrityError):
            Port.no_workspace_objects.create(
                node=node,
                key="output1",
                display_name="Same Name",  # Duplicate display_name
                direction=PortDirection.OUTPUT,
            )

    def test_display_name_same_across_different_nodes(self, db, node, second_node):
        """Same display_name allowed on different nodes."""
        port1 = Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="Response",
            direction=PortDirection.OUTPUT,
        )
        port2 = Port.no_workspace_objects.create(
            node=second_node,
            key="output1",
            display_name="Response",
            direction=PortDirection.OUTPUT,
        )
        assert port1.display_name == port2.display_name
        assert port1.node != port2.node

    def test_display_name_unique_allows_deleted(self, db, node):
        """Soft-deleted ports don't block display_name uniqueness."""
        port1 = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Reusable Name",
            direction=PortDirection.INPUT,
        )
        port1.delete()  # Soft delete

        port2 = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="Reusable Name",
            direction=PortDirection.INPUT,
        )
        assert port2.display_name == "Reusable Name"


@pytest.mark.unit
class TestPortCustomKeyConstraint:
    """Tests for key='custom' exemption from unique_node_port_key constraint."""

    def test_multiple_custom_key_ports_allowed(self, db, dynamic_node):
        """Multiple ports with key='custom' on the same node are allowed."""
        port1 = Port.no_workspace_objects.create(
            node=dynamic_node,
            key="custom",
            display_name="Custom Port 1",
            direction=PortDirection.INPUT,
        )
        port2 = Port.no_workspace_objects.create(
            node=dynamic_node,
            key="custom",
            display_name="Custom Port 2",
            direction=PortDirection.INPUT,
        )
        assert port1.key == "custom"
        assert port2.key == "custom"
        assert port1.id != port2.id

    def test_non_custom_key_still_unique(self, db, node):
        """Non-custom keys are still unique per node."""
        Port.no_workspace_objects.create(
            node=node,
            key="output1",
            display_name="Response 1",
            direction=PortDirection.OUTPUT,
        )
        with pytest.raises(IntegrityError):
            Port.no_workspace_objects.create(
                node=node,
                key="output1",  # Duplicate non-custom key
                display_name="Response 2",
                direction=PortDirection.OUTPUT,
            )


@pytest.mark.unit
class TestPortRefPort:
    """Tests for Port.ref_port FK and validation."""

    def test_ref_port_valid_on_subgraph_node(
        self, db, subgraph_node, active_referenced_graph_version, node_template
    ):
        """ref_port on a subgraph node port pointing to child graph port is valid."""
        # Create a port in the child graph
        child_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Child Node",
            config={},
        )
        child_port = Port.no_workspace_objects.create(
            node=child_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        # Create a subgraph node port with ref_port
        subgraph_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="My Input",
            direction=PortDirection.INPUT,
            ref_port=child_port,
        )
        subgraph_port.save()

        assert subgraph_port.ref_port == child_port
        assert subgraph_port.ref_port_id == child_port.id

    def test_ref_port_rejected_on_atomic_node(self, db, node, second_node):
        """ref_port is not allowed on atomic node ports."""
        other_port = Port.no_workspace_objects.create(
            node=second_node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )

        port = Port(
            node=node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
            ref_port=other_port,
        )
        with pytest.raises(ValidationError, match="only allowed on ports of subgraph"):
            port.save()

    def test_ref_port_direction_mismatch_rejected(
        self, db, subgraph_node, active_referenced_graph_version, node_template
    ):
        """ref_port with direction mismatch is rejected."""
        child_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Child Node",
            config={},
        )
        child_output_port = Port.no_workspace_objects.create(
            node=child_node,
            key="output1",
            display_name="output1",
            direction=PortDirection.OUTPUT,
        )

        # Try to create an INPUT port referencing an OUTPUT child port
        port = Port(
            node=subgraph_node,
            key="custom",
            display_name="My Input",
            direction=PortDirection.INPUT,
            ref_port=child_output_port,
        )
        with pytest.raises(ValidationError, match="direction mismatch"):
            port.save()

    def test_ref_port_wrong_graph_version_rejected(
        self,
        db,
        subgraph_node,
        graph_version,
        node_template,
    ):
        """ref_port must belong to a node in the referenced graph version."""
        # Create a port in the SAME graph version (not the child graph)
        same_graph_node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Same Graph Node",
            config={},
        )
        wrong_port = Port.no_workspace_objects.create(
            node=same_graph_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        port = Port(
            node=subgraph_node,
            key="custom",
            display_name="My Input",
            direction=PortDirection.INPUT,
            ref_port=wrong_port,
        )
        with pytest.raises(ValidationError, match="referenced graph version"):
            port.save()

    def test_ref_port_null_by_default(self, db, node):
        """ref_port defaults to None."""
        port = Port.no_workspace_objects.create(
            node=node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )
        assert port.ref_port is None
        assert port.ref_port_id is None

    def test_two_subgraph_nodes_same_graph_different_ref_ports(
        self,
        db,
        graph_version,
        active_referenced_graph_version,
        node_template,
    ):
        """Two subgraph nodes referencing the same graph can have ports
        with different display_names pointing to the same child ports."""
        # Create child graph port
        child_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Child Node",
            config={},
        )
        child_input = Port.no_workspace_objects.create(
            node=child_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        # Create two subgraph nodes referencing the same graph
        subgraph_node_1 = Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Instance 1",
            config={},
        )
        subgraph_node_2 = Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Instance 2",
            config={},
        )

        # Both can have ports referencing the same child port
        port1 = Port(
            node=subgraph_node_1,
            key="custom",
            display_name="First Input",
            direction=PortDirection.INPUT,
            ref_port=child_input,
        )
        port1.save()

        port2 = Port(
            node=subgraph_node_2,
            key="custom",
            display_name="Second Input",
            direction=PortDirection.INPUT,
            ref_port=child_input,
        )
        port2.save()

        assert port1.ref_port_id == child_input.id
        assert port2.ref_port_id == child_input.id
        assert port1.display_name != port2.display_name

    def test_ref_port_set_null_on_delete(
        self, db, subgraph_node, active_referenced_graph_version, node_template
    ):
        """When the referenced port is hard-deleted, ref_port is set to NULL."""
        child_node = Node.no_workspace_objects.create(
            graph_version=active_referenced_graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Child Node",
            config={},
        )
        child_port = Port.no_workspace_objects.create(
            node=child_node,
            key="input1",
            display_name="input1",
            direction=PortDirection.INPUT,
        )

        subgraph_port = Port(
            node=subgraph_node,
            key="custom",
            display_name="My Input",
            direction=PortDirection.INPUT,
            ref_port=child_port,
        )
        subgraph_port.save()

        # Hard delete the child port
        Port.all_objects.filter(id=child_port.id).delete()

        subgraph_port.refresh_from_db()
        assert subgraph_port.ref_port_id is None


@pytest.mark.unit
class TestPortDisplayNameCharValidation:
    """Tests for _validate_display_name_chars — reserved characters in output port names."""

    @pytest.mark.parametrize("char", [".", "[", "]", "{", "}"])
    def test_rejects_reserved_char_in_output_display_name(self, db, dynamic_node, char):
        port = Port(
            node=dynamic_node,
            key="custom",
            display_name=f"bad{char}name",
            direction=PortDirection.OUTPUT,
        )
        with pytest.raises(ValidationError, match="reserved characters"):
            port.clean()

    def test_allows_reserved_char_in_input_display_name(self, db, dynamic_node):
        """Input ports are allowed to have dot-notation display_names."""
        port = Port(
            node=dynamic_node,
            key="custom",
            display_name="Node1.response.data",
            direction=PortDirection.INPUT,
        )
        port.clean()  # should not raise

    def test_allows_clean_output_display_name(self, db, dynamic_node):
        port = Port(
            node=dynamic_node,
            key="custom",
            display_name="good_name",
            direction=PortDirection.OUTPUT,
        )
        port.clean()  # should not raise

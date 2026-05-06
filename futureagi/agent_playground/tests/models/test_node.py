"""
Tests for the Node model.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models

from agent_playground.models import Graph, GraphVersion, Node, NodeTemplate
from agent_playground.models.choices import (
    GraphVersionStatus,
    NodeType,
    PortMode,
)


@pytest.mark.unit
class TestAtomicNodeCreation:
    """Tests for atomic Node creation."""

    def test_atomic_node_creation_success(self, db, graph_version, node_template):
        """Atomic node with template."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Atomic Node",
            config={},
            position={"x": 0, "y": 0},
        )
        node.clean()
        assert node.id is not None
        assert node.type == NodeType.ATOMIC
        assert node.node_template == node_template

    def test_atomic_node_requires_template(self, db, graph_version):
        """type=ATOMIC must have node_template."""
        node = Node(
            graph_version=graph_version,
            node_template=None,  # Missing template
            type=NodeType.ATOMIC,
            name="Invalid Atomic",
            config={},
        )
        with pytest.raises(
            ValidationError, match="Atomic nodes must have node_template set"
        ):
            node.clean()

    def test_atomic_node_forbids_ref_graph_version(
        self, db, graph_version, node_template, active_referenced_graph_version
    ):
        """type=ATOMIC cannot have ref_graph_version."""
        node = Node(
            graph_version=graph_version,
            node_template=node_template,
            ref_graph_version=active_referenced_graph_version,  # Should not be set
            type=NodeType.ATOMIC,
            name="Invalid Atomic",
            config={},
        )
        with pytest.raises(
            ValidationError, match="Atomic nodes cannot have ref_graph_version"
        ):
            node.clean()


@pytest.mark.unit
class TestAtomicNodeConfigValidation:
    """Tests for atomic node config validation."""

    def test_atomic_node_config_validates_against_schema(self, db, graph_version):
        """Config must match template schema."""
        template = NodeTemplate.no_workspace_objects.create(
            name="schema_template",
            display_name="Schema Template",
            description="Template with schema",
            categories=["test"],
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        )
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Valid Config Node",
            config={"name": "test"},  # Valid config
        )
        node.clean()  # Should not raise
        assert node.config["name"] == "test"

    def test_atomic_node_invalid_config_fails(self, db, graph_version):
        """Invalid config raises ValidationError."""
        template = NodeTemplate.no_workspace_objects.create(
            name="strict_schema_template",
            display_name="Strict Schema Template",
            description="Template with strict schema",
            categories=["test"],
            input_definition=[],
            output_definition=[],
            input_mode=PortMode.DYNAMIC,
            output_mode=PortMode.DYNAMIC,
            config_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                },
                "required": ["count"],
            },
        )
        node = Node(
            graph_version=graph_version,
            node_template=template,
            type=NodeType.ATOMIC,
            name="Invalid Config Node",
            config={"count": "not_an_integer"},  # Invalid - should be integer
        )
        with pytest.raises(ValidationError, match="Invalid config"):
            node.clean()


@pytest.mark.unit
class TestSubgraphNodeCreation:
    """Tests for subgraph Node creation."""

    def test_subgraph_node_creation_success(
        self, db, graph_version, active_referenced_graph_version
    ):
        """Subgraph node with ref_graph_version."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph Node",
            config={},
        )
        node.clean()
        assert node.id is not None
        assert node.type == NodeType.SUBGRAPH
        assert node.ref_graph_version == active_referenced_graph_version

    def test_subgraph_node_requires_ref_graph_version(self, db, graph_version):
        """type=SUBGRAPH must have ref_graph_version."""
        node = Node(
            graph_version=graph_version,
            ref_graph_version=None,  # Missing ref
            type=NodeType.SUBGRAPH,
            name="Invalid Subgraph",
            config={},
        )
        with pytest.raises(
            ValidationError, match="Subgraph nodes must have ref_graph_version set"
        ):
            node.clean()

    def test_subgraph_node_forbids_template(
        self, db, graph_version, node_template, active_referenced_graph_version
    ):
        """type=SUBGRAPH cannot have node_template."""
        node = Node(
            graph_version=graph_version,
            node_template=node_template,  # Should not be set
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="Invalid Subgraph",
            config={},
        )
        with pytest.raises(
            ValidationError, match="Subgraph nodes cannot have node_template"
        ):
            node.clean()

    def test_subgraph_node_requires_empty_config(
        self, db, graph_version, active_referenced_graph_version
    ):
        """Subgraph nodes must have empty config."""
        node = Node(
            graph_version=graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="Subgraph With Config",
            config={"some": "config"},  # Should be empty
        )
        with pytest.raises(
            ValidationError, match="Subgraph nodes must have empty config"
        ):
            node.clean()


@pytest.mark.unit
class TestSubgraphNodeReferenceValidation:
    """Tests for subgraph node reference validation."""

    def test_subgraph_node_no_self_reference(self, db, organization, workspace, user):
        """Cannot reference own graph."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Self Ref Graph",
            created_by=user,
        )
        version1 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )
        version2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.ACTIVE,
        )

        node = Node(
            graph_version=version1,
            ref_graph_version=version2,  # Same graph
            type=NodeType.SUBGRAPH,
            name="Self Reference",
            config={},
        )
        with pytest.raises(
            ValidationError,
            match="Subgraph nodes cannot reference versions of the same graph",
        ):
            node.clean()

    def test_subgraph_node_ref_must_be_validated(
        self, db, graph_version, referenced_graph_version
    ):
        """ref_graph_version.status must be ACTIVE or INACTIVE (not DRAFT)."""
        # referenced_graph_version is DRAFT by default
        node = Node(
            graph_version=graph_version,
            ref_graph_version=referenced_graph_version,  # Draft, not validated
            type=NodeType.SUBGRAPH,
            name="Draft Reference",
            config={},
        )
        with pytest.raises(
            ValidationError,
            match="Subgraph nodes can only reference active or inactive versions",
        ):
            node.clean()

    def test_subgraph_node_can_reference_inactive(
        self, db, graph_version, referenced_graph
    ):
        """Subgraph nodes can reference inactive versions (previously active)."""
        # Create an inactive version
        inactive_version = GraphVersion.no_workspace_objects.create(
            graph=referenced_graph,
            version_number=3,
            status=GraphVersionStatus.INACTIVE,
        )

        # Should succeed
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=inactive_version,
            type=NodeType.SUBGRAPH,
            name="Inactive Reference",
            config={},
        )
        node.clean()  # Should not raise
        assert node.ref_graph_version == inactive_version
        assert node.ref_graph_version.status == GraphVersionStatus.INACTIVE

    @pytest.mark.parametrize(
        "status,should_pass",
        [
            (GraphVersionStatus.ACTIVE, True),
            (GraphVersionStatus.INACTIVE, True),
            (GraphVersionStatus.DRAFT, False),
        ],
    )
    def test_subgraph_node_reference_by_status(
        self, db, graph_version, referenced_graph, status, should_pass
    ):
        """Test subgraph node validation across all GraphVersion statuses."""
        test_version = GraphVersion.no_workspace_objects.create(
            graph=referenced_graph,
            version_number=10,
            status=status,
        )

        node = Node(
            graph_version=graph_version,
            ref_graph_version=test_version,
            type=NodeType.SUBGRAPH,
            name=f"Ref {status}",
            config={},
        )

        if should_pass:
            node.clean()  # Should not raise
            assert node.ref_graph_version.status == status
        else:
            with pytest.raises(
                ValidationError,
                match="Subgraph nodes can only reference active or inactive versions",
            ):
                node.clean()

    def test_subgraph_node_single_version_per_graph(
        self, db, graph_version, active_referenced_graph_version, referenced_graph
    ):
        """Only one version of external graph can be referenced per graph_version."""
        # Create first subgraph node
        Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=active_referenced_graph_version,
            type=NodeType.SUBGRAPH,
            name="First Subgraph",
            config={},
        )

        # Deactivate the first active version before creating another active one
        active_referenced_graph_version.status = GraphVersionStatus.INACTIVE
        active_referenced_graph_version.save()

        # Create another active version of the same referenced graph
        another_version = GraphVersion.no_workspace_objects.create(
            graph=referenced_graph,
            version_number=3,
            status=GraphVersionStatus.ACTIVE,
        )

        # Try to create second subgraph node referencing different version of same graph
        node2 = Node(
            graph_version=graph_version,
            ref_graph_version=another_version,
            type=NodeType.SUBGRAPH,
            name="Second Subgraph",
            config={},
        )
        with pytest.raises(
            ValidationError, match="This graph already references a different version"
        ):
            node2.clean()


@pytest.mark.unit
class TestCrossGraphCycleDetection:
    """Tests for cross-graph cycle detection in subgraph nodes."""

    def test_direct_cycle_rejected(self, db, organization, workspace, user):
        """A refs B, B refs A -> rejected."""
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

        version_a = GraphVersion.no_workspace_objects.create(
            graph=graph_a, version_number=1, status=GraphVersionStatus.DRAFT
        )
        active_version_a = GraphVersion.no_workspace_objects.create(
            graph=graph_a, version_number=2, status=GraphVersionStatus.ACTIVE
        )
        version_b = GraphVersion.no_workspace_objects.create(
            graph=graph_b, version_number=1, status=GraphVersionStatus.DRAFT
        )
        active_version_b = GraphVersion.no_workspace_objects.create(
            graph=graph_b, version_number=2, status=GraphVersionStatus.ACTIVE
        )

        # A refs B (succeeds)
        Node.no_workspace_objects.create(
            graph_version=version_a,
            ref_graph_version=active_version_b,
            type=NodeType.SUBGRAPH,
            name="A -> B",
            config={},
        )

        # B refs A (should fail - cycle)
        node_ba = Node(
            graph_version=version_b,
            ref_graph_version=active_version_a,
            type=NodeType.SUBGRAPH,
            name="B -> A",
            config={},
        )
        with pytest.raises(ValidationError, match="circular dependency"):
            node_ba.clean()

    def test_transitive_cycle_rejected(self, db, organization, workspace, user):
        """A->B->C, C->A -> rejected."""
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
        graph_c = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Graph C",
            created_by=user,
        )

        ver_a = GraphVersion.no_workspace_objects.create(
            graph=graph_a, version_number=1, status=GraphVersionStatus.DRAFT
        )
        active_a = GraphVersion.no_workspace_objects.create(
            graph=graph_a, version_number=2, status=GraphVersionStatus.ACTIVE
        )
        ver_b = GraphVersion.no_workspace_objects.create(
            graph=graph_b, version_number=1, status=GraphVersionStatus.DRAFT
        )
        active_b = GraphVersion.no_workspace_objects.create(
            graph=graph_b, version_number=2, status=GraphVersionStatus.ACTIVE
        )
        ver_c = GraphVersion.no_workspace_objects.create(
            graph=graph_c, version_number=1, status=GraphVersionStatus.DRAFT
        )
        active_c = GraphVersion.no_workspace_objects.create(
            graph=graph_c, version_number=2, status=GraphVersionStatus.ACTIVE
        )

        # A -> B
        Node.no_workspace_objects.create(
            graph_version=ver_a,
            ref_graph_version=active_b,
            type=NodeType.SUBGRAPH,
            name="A -> B",
            config={},
        )
        # B -> C
        Node.no_workspace_objects.create(
            graph_version=ver_b,
            ref_graph_version=active_c,
            type=NodeType.SUBGRAPH,
            name="B -> C",
            config={},
        )
        # C -> A should fail
        node_ca = Node(
            graph_version=ver_c,
            ref_graph_version=active_a,
            type=NodeType.SUBGRAPH,
            name="C -> A",
            config={},
        )
        with pytest.raises(ValidationError, match="circular dependency"):
            node_ca.clean()

    def test_diamond_no_cycle_allowed(self, db, organization, workspace, user):
        """A->B, A->C, B->D, C->D -> allowed (diamond, no cycle)."""
        graphs = {}
        versions = {}
        active_versions = {}
        for name in ["A", "B", "C", "D"]:
            g = Graph.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                name=f"Graph {name}",
                created_by=user,
            )
            graphs[name] = g
            versions[name] = GraphVersion.no_workspace_objects.create(
                graph=g,
                version_number=1,
                status=GraphVersionStatus.DRAFT,
            )
            active_versions[name] = GraphVersion.no_workspace_objects.create(
                graph=g,
                version_number=2,
                status=GraphVersionStatus.ACTIVE,
            )

        # A->B, A->C
        Node.no_workspace_objects.create(
            graph_version=versions["A"],
            ref_graph_version=active_versions["B"],
            type=NodeType.SUBGRAPH,
            name="A -> B",
            config={},
        )
        Node.no_workspace_objects.create(
            graph_version=versions["A"],
            ref_graph_version=active_versions["C"],
            type=NodeType.SUBGRAPH,
            name="A -> C",
            config={},
        )
        # B->D
        Node.no_workspace_objects.create(
            graph_version=versions["B"],
            ref_graph_version=active_versions["D"],
            type=NodeType.SUBGRAPH,
            name="B -> D",
            config={},
        )
        # C->D should succeed (diamond, not a cycle)
        node_cd = Node.no_workspace_objects.create(
            graph_version=versions["C"],
            ref_graph_version=active_versions["D"],
            type=NodeType.SUBGRAPH,
            name="C -> D",
            config={},
        )
        node_cd.clean()  # Should not raise


@pytest.mark.unit
class TestSubgraphNodeTemplateReference:
    """Tests for subgraph nodes referencing template graphs."""

    def test_subgraph_referencing_template_active_version(
        self, db, graph_version, active_template_graph_version
    ):
        """Subgraph node referencing template's active version succeeds."""
        node = Node.no_workspace_objects.create(
            graph_version=graph_version,
            ref_graph_version=active_template_graph_version,
            type=NodeType.SUBGRAPH,
            name="Template Ref",
            config={},
        )
        node.clean()  # Should not raise
        assert node.ref_graph_version == active_template_graph_version

    def test_subgraph_referencing_template_non_active_version_fails(
        self, db, graph_version, template_graph
    ):
        """Subgraph node referencing template's non-active version fails."""
        draft_version = GraphVersion.no_workspace_objects.create(
            graph=template_graph,
            version_number=2,
            status=GraphVersionStatus.DRAFT,
        )
        node = Node(
            graph_version=graph_version,
            ref_graph_version=draft_version,
            type=NodeType.SUBGRAPH,
            name="Bad Template Ref",
            config={},
        )
        with pytest.raises(
            ValidationError,
            match="Subgraph nodes can only reference active or inactive versions",
        ):
            node.clean()


@pytest.mark.unit
class TestNodeCascadeDelete:
    """Tests for Node cascade delete behavior."""

    def test_node_cascade_delete_graph_version(self, db, node, graph_version):
        """Deleting version cascades to nodes."""
        node_id = node.id

        # Hard delete the graph version
        GraphVersion.all_objects.filter(id=graph_version.id).delete()

        # Node should be gone
        assert not Node.all_objects.filter(id=node_id).exists()


@pytest.mark.unit
class TestNodeNameCharValidation:
    """Tests for _validate_name_chars — reserved characters in node names."""

    @pytest.mark.parametrize("char", [".", "[", "]", "{", "}"])
    def test_rejects_reserved_char_in_name(
        self, db, graph_version, node_template, char
    ):
        node = Node(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name=f"Bad{char}Name",
            config={},
        )
        with pytest.raises(ValidationError, match="reserved characters"):
            node.clean()

    def test_allows_clean_name(self, db, graph_version, node_template):
        node = Node(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="Good_Name-123",
            config={},
        )
        node.clean()  # should not raise

    def test_allows_spaces_and_dashes(self, db, graph_version, node_template):
        node = Node(
            graph_version=graph_version,
            node_template=node_template,
            type=NodeType.ATOMIC,
            name="My Node - v2",
            config={},
        )
        node.clean()  # should not raise

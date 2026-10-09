"""Comprehensive tests for graph serializers."""

import uuid

import pytest

from agent_playground.models.choices import GraphVersionStatus
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.serializers.graph import (
    BulkDeleteSerializer,
    GraphCreateSerializer,
    GraphDetailSerializer,
    GraphListSerializer,
    GraphUpdateSerializer,
    ReferenceableGraphSerializer,
)


class TestGraphListSerializer:
    """Tests for GraphListSerializer."""

    def test_serializes_list_fields(self, graph, user):
        """Test that lightweight list fields are serialized correctly."""
        serializer = GraphListSerializer(graph)
        data = serializer.data

        assert data["id"] == str(graph.id)
        assert data["name"] == graph.name
        assert data["description"] == graph.description
        assert data["is_template"] == graph.is_template
        assert data["max_concurrent_nodes"] == 10
        assert "created_at" in data
        assert "updated_at" in data
        assert data["created_by"]["id"] == str(user.id)
        assert data["created_by"]["email"] == user.email

    def test_active_version_number_with_active_version(
        self, graph, active_graph_version, db
    ):
        """Test that active_version_number is returned when active version exists."""
        from agent_playground.utils.graph import annotate_graph_list_fields

        annotated = annotate_graph_list_fields(
            Graph.no_workspace_objects.filter(pk=graph.pk)
        ).get()
        serializer = GraphListSerializer(annotated)
        data = serializer.data

        assert data["active_version_number"] == active_graph_version.version_number

    def test_active_version_number_without_active_version(self, graph, graph_version):
        """Test that active_version_number is None when no active version exists."""
        serializer = GraphListSerializer(graph)
        data = serializer.data

        assert data["active_version_number"] is None

    def test_excludes_detailed_fields(self, graph):
        """Test that detailed fields are not included in list serializer."""
        serializer = GraphListSerializer(graph)
        data = serializer.data

        assert "active_version" not in data

    def test_serializes_many_graphs(self, graph, referenced_graph):
        """Test serialization of multiple graphs."""
        serializer = GraphListSerializer([graph, referenced_graph], many=True)
        data = serializer.data

        assert len(data) == 2

    def test_serializes_empty_list(self):
        """Test serialization of empty graph list."""
        serializer = GraphListSerializer([], many=True)
        data = serializer.data
        assert data == []

    def test_all_fields_are_read_only(self):
        """Test that all fields in GraphListSerializer are read-only."""
        serializer = GraphListSerializer()
        for field_name in serializer.fields:
            assert serializer.fields[field_name].read_only

    def test_id_is_string_uuid(self, graph):
        """Test that id is serialized as string UUID."""
        serializer = GraphListSerializer(graph)
        data = serializer.data
        assert isinstance(data["id"], str)
        uuid.UUID(data["id"])  # Should not raise

    def test_serializes_template_graph(self, template_graph):
        """Test serialization of template graph."""
        serializer = GraphListSerializer(template_graph)
        data = serializer.data

        assert data["is_template"] is True
        assert data["created_by"] is None

    def test_serializes_graph_with_null_description(
        self, organization, workspace, user, db
    ):
        """Test serialization of graph with null description."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="No Description Graph",
            description=None,
            created_by=user,
        )
        serializer = GraphListSerializer(graph)
        assert serializer.data["description"] is None

    def test_serializes_graph_with_empty_description(
        self, organization, workspace, user, db
    ):
        """Test serialization of graph with empty description."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Empty Description Graph",
            description="",
            created_by=user,
        )
        serializer = GraphListSerializer(graph)
        assert serializer.data["description"] == ""

    def test_created_by_includes_required_fields(self, graph, user):
        """Test that created_by includes id and email."""
        serializer = GraphListSerializer(graph)
        data = serializer.data

        assert "id" in data["created_by"]
        assert "email" in data["created_by"]
        assert data["created_by"]["id"] == str(user.id)
        assert data["created_by"]["email"] == user.email


class TestGraphDetailSerializer:
    """Tests for GraphDetailSerializer."""

    def test_serializes_all_fields(self, graph, user):
        """Test that all fields are serialized correctly."""
        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        assert data["id"] == str(graph.id)
        assert data["name"] == graph.name
        assert data["description"] == graph.description
        assert data["is_template"] == graph.is_template
        assert data["max_concurrent_nodes"] == 10
        assert "created_at" in data
        assert "updated_at" in data
        assert "active_version" in data

    def test_returns_latest_version(self, graph, active_graph_version, graph_version):
        """Test that the latest version (highest version_number) is returned."""
        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        # active_graph_version has version_number=2 > graph_version's version_number=1
        assert data["active_version"] is not None
        assert data["active_version"]["id"] == str(active_graph_version.id)
        assert data["active_version"]["version_number"] == 2

    def test_returns_latest_draft_when_no_active(self, graph, graph_version):
        """Test that latest draft is returned when no active version."""
        # Create another draft with higher version number
        draft2 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=2,
            status=GraphVersionStatus.DRAFT,
        )

        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        assert data["active_version"] is not None
        assert data["active_version"]["id"] == str(draft2.id)
        assert data["active_version"]["status"] == GraphVersionStatus.DRAFT

    def test_returns_none_when_no_versions(self, organization, workspace, user):
        """Test that active_version is None when no versions exist."""
        graph = Graph.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            name="Empty Graph",
            created_by=user,
        )
        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        assert data["active_version"] is None

    def test_excludes_user_fields(self, graph, user):
        """Test that created_by and collaborators are not in detail serializer."""
        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        assert "created_by" not in data
        assert "collaborators" not in data

    def test_active_version_includes_nodes(
        self, graph, active_graph_version, node_in_active_version
    ):
        """Test that active_version includes nodes."""
        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        assert data["active_version"] is not None
        assert "nodes" in data["active_version"]

    def test_returns_latest_version_when_multiple_exist(
        self, graph, graph_version, active_graph_version
    ):
        """Test that the version with the highest version_number is returned."""
        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        # active_graph_version has version_number=2, graph_version has version_number=1
        assert data["active_version"]["id"] == str(active_graph_version.id)
        assert data["active_version"]["version_number"] == 2

    def test_returns_draft_with_higher_version_over_active(
        self, graph, active_graph_version
    ):
        """Test that a draft with a higher version_number is preferred over an active version."""
        # active_graph_version has version_number=2, status=ACTIVE
        draft_v3 = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=3,
            status=GraphVersionStatus.DRAFT,
        )

        serializer = GraphDetailSerializer(graph)
        data = serializer.data

        assert data["active_version"] is not None
        assert data["active_version"]["id"] == str(draft_v3.id)
        assert data["active_version"]["status"] == GraphVersionStatus.DRAFT
        assert data["active_version"]["version_number"] == 3

    def test_detail_includes_common_fields(self, graph):
        """Test that detail serializer includes common graph fields."""
        detail_serializer = GraphDetailSerializer(graph)
        detail_data = detail_serializer.data

        # Core fields should be present in detail
        for field in (
            "id",
            "name",
            "description",
            "is_template",
            "created_at",
            "updated_at",
        ):
            assert field in detail_data

    def test_serializes_template_graph(
        self, template_graph, active_template_graph_version
    ):
        """Test serialization of template graph detail."""
        serializer = GraphDetailSerializer(template_graph)
        data = serializer.data

        assert data["is_template"] is True
        assert data["active_version"] is not None


class TestGraphCreateSerializer:
    """Tests for GraphCreateSerializer."""

    def test_valid_data(self):
        """Test validation of valid create data."""
        data = {
            "name": "New Graph",
            "description": "A new graph description",
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "New Graph"
        assert serializer.validated_data["description"] == "A new graph description"

    def test_name_required(self):
        """Test that name is required."""
        data = {
            "description": "A description",
        }
        serializer = GraphCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_description_optional(self):
        """Test that description is optional."""
        data = {
            "name": "New Graph",
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data.get("description") is None
        assert "max_concurrent_nodes" not in serializer.validated_data

    def test_max_concurrent_nodes_optional_and_validated(self):
        serializer = GraphCreateSerializer(
            data={"name": "New Graph", "max_concurrent_nodes": 7}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["max_concurrent_nodes"] == 7

        serializer = GraphCreateSerializer(
            data={"name": "New Graph", "max_concurrent_nodes": 0}
        )
        assert not serializer.is_valid()
        assert "greater than zero" in str(serializer.errors)

    def test_empty_description_allowed(self):
        """Test that empty description is allowed."""
        data = {
            "name": "New Graph",
            "description": "",
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_empty_name_fails(self):
        """Test that empty name fails validation."""
        data = {
            "name": "",
            "description": "Some description",
        }
        serializer = GraphCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_whitespace_only_name_fails(self):
        """Test that whitespace-only name fails validation."""
        data = {
            "name": "   ",
        }
        serializer = GraphCreateSerializer(data=data)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_name_with_special_characters(self):
        """Test that name with special characters is valid."""
        data = {
            "name": "My Graph - v2.0 (Beta)",
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_unicode_name(self):
        """Test that unicode name is valid."""
        data = {
            "name": "图表名称 - Gráfico",
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_long_name_accepted(self):
        """Test that long name is accepted."""
        data = {
            "name": "A" * 255,
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_long_description_accepted(self):
        """Test that long description is accepted."""
        data = {
            "name": "Test Graph",
            "description": "D" * 5000,
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_extra_fields_ignored(self):
        """Test that extra fields are ignored."""
        data = {
            "name": "New Graph",
            "is_template": True,  # Should be ignored
            "extra_field": "value",
        }
        serializer = GraphCreateSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert "is_template" not in serializer.validated_data
        assert "extra_field" not in serializer.validated_data


class TestGraphUpdateSerializer:
    """Tests for GraphUpdateSerializer."""

    def test_valid_update_name(self):
        """Test validation of name update."""
        data = {
            "name": "Updated Graph Name",
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["name"] == "Updated Graph Name"

    def test_valid_update_description(self):
        """Test validation of description update."""
        data = {
            "description": "Updated description",
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["description"] == "Updated description"

    def test_valid_update_both(self):
        """Test validation of both fields update."""
        data = {
            "name": "Updated Name",
            "description": "Updated description",
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors

    def test_empty_data_valid(self):
        """Test that empty data is valid for partial update."""
        serializer = GraphUpdateSerializer(data={}, partial=True)
        assert serializer.is_valid(), serializer.errors

    def test_null_description_allowed(self):
        """Test that null description is allowed."""
        data = {
            "description": None,
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors

    def test_empty_name_fails(self):
        """Test that empty name fails validation."""
        data = {
            "name": "",
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_whitespace_only_name_fails(self):
        """Test that whitespace-only name fails validation."""
        data = {
            "name": "   ",
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_extra_fields_ignored(self):
        """Test that extra fields are ignored in update."""
        data = {
            "name": "Updated Name",
            "is_template": True,  # Should be ignored
            "created_by": "someone",  # Should be ignored
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert "is_template" not in serializer.validated_data
        assert "created_by" not in serializer.validated_data

    def test_clear_description(self):
        """Test clearing description with empty string."""
        data = {
            "description": "",
        }
        serializer = GraphUpdateSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["description"] == ""

    def test_valid_update_max_concurrent_nodes(self):
        serializer = GraphUpdateSerializer(
            data={"max_concurrent_nodes": 3}, partial=True
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["max_concurrent_nodes"] == 3

    def test_rejects_zero_max_concurrent_nodes(self):
        serializer = GraphUpdateSerializer(
            data={"max_concurrent_nodes": 0}, partial=True
        )
        assert not serializer.is_valid()
        assert "max_concurrent_nodes" in serializer.errors
        assert "greater than zero" in str(serializer.errors)

    def test_rejects_negative_max_concurrent_nodes(self):
        serializer = GraphUpdateSerializer(
            data={"max_concurrent_nodes": -5}, partial=True
        )
        assert not serializer.is_valid()
        assert "greater than zero" in str(serializer.errors)

    def test_rejects_non_integer_max_concurrent_nodes(self):
        serializer = GraphUpdateSerializer(
            data={"max_concurrent_nodes": "abc"}, partial=True
        )
        assert not serializer.is_valid()
        assert "greater than zero" in str(serializer.errors)


class TestReferenceableGraphSerializer:
    """Tests for ReferenceableGraphSerializer."""

    def test_serializes_fields(self, graph, active_graph_version):
        """Test that all fields are serialized correctly."""
        data = {
            "id": graph.id,
            "name": graph.name,
            "description": graph.description,
            "is_template": False,
            "versions": [
                {
                    "id": active_graph_version.id,
                    "version_number": active_graph_version.version_number,
                    "status": active_graph_version.status,
                    "exposed_ports": [],
                }
            ],
        }
        serializer = ReferenceableGraphSerializer(data)
        result = serializer.data

        assert result["id"] == str(graph.id)
        assert result["name"] == graph.name
        assert result["description"] == graph.description
        assert result["is_template"] is False
        assert "versions" in result
        assert len(result["versions"]) == 1
        assert result["versions"][0]["id"] == str(active_graph_version.id)
        assert (
            result["versions"][0]["version_number"]
            == active_graph_version.version_number
        )
        assert result["versions"][0]["status"] == active_graph_version.status

    def test_serializes_many(
        self,
        graph,
        referenced_graph,
        active_graph_version,
        active_referenced_graph_version,
    ):
        """Test serialization of multiple graphs."""
        data = [
            {
                "id": graph.id,
                "name": graph.name,
                "description": graph.description,
                "is_template": False,
                "versions": [
                    {
                        "id": active_graph_version.id,
                        "version_number": active_graph_version.version_number,
                        "status": active_graph_version.status,
                        "exposed_ports": [],
                    }
                ],
            },
            {
                "id": referenced_graph.id,
                "name": referenced_graph.name,
                "description": referenced_graph.description,
                "is_template": False,
                "versions": [
                    {
                        "id": active_referenced_graph_version.id,
                        "version_number": active_referenced_graph_version.version_number,
                        "status": active_referenced_graph_version.status,
                        "exposed_ports": [],
                    }
                ],
            },
        ]
        serializer = ReferenceableGraphSerializer(data, many=True)
        result = serializer.data

        assert len(result) == 2

    def test_serializes_empty_list(self):
        """Test serialization of empty graph list."""
        serializer = ReferenceableGraphSerializer([], many=True)
        result = serializer.data
        assert result == []

    def test_serializes_graph_with_null_description(self, graph, active_graph_version):
        """Test serialization when description is null."""
        data = {
            "id": graph.id,
            "name": graph.name,
            "description": None,
            "is_template": False,
            "versions": [
                {
                    "id": active_graph_version.id,
                    "version_number": active_graph_version.version_number,
                    "status": active_graph_version.status,
                    "exposed_ports": [],
                }
            ],
        }
        serializer = ReferenceableGraphSerializer(data)
        result = serializer.data
        assert result["description"] is None

    def test_all_fields_are_read_only(self):
        """Test that all fields are read-only."""
        serializer = ReferenceableGraphSerializer()
        for field_name in serializer.fields:
            assert serializer.fields[field_name].read_only

    def test_id_is_string_uuid(self, graph, active_graph_version):
        """Test that UUIDs are serialized as strings."""
        data = {
            "id": graph.id,
            "name": graph.name,
            "description": graph.description,
            "is_template": False,
            "versions": [
                {
                    "id": active_graph_version.id,
                    "version_number": active_graph_version.version_number,
                    "status": active_graph_version.status,
                    "exposed_ports": [],
                }
            ],
        }
        serializer = ReferenceableGraphSerializer(data)
        result = serializer.data

        assert isinstance(result["id"], str)
        uuid.UUID(result["id"])  # Should not raise
        assert isinstance(result["versions"][0]["id"], str)
        uuid.UUID(result["versions"][0]["id"])  # Should not raise


class TestBulkDeleteSerializer:
    """Tests for BulkDeleteSerializer."""

    def test_valid_with_ids(self):
        """Test valid data with a list of IDs."""
        data = {"ids": [str(uuid.uuid4()), str(uuid.uuid4())]}
        serializer = BulkDeleteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["ids"]) == 2

    def test_valid_with_select_all(self):
        """Test valid data with select_all=True."""
        data = {"select_all": True}
        serializer = BulkDeleteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["select_all"] is True

    def test_valid_select_all_with_exclude_ids(self):
        """Test valid data with select_all and exclude_ids."""
        excluded = str(uuid.uuid4())
        data = {"select_all": True, "exclude_ids": [excluded]}
        serializer = BulkDeleteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["exclude_ids"]) == 1

    def test_invalid_both_select_all_and_ids(self):
        """Test that providing both select_all and ids fails."""
        data = {"select_all": True, "ids": [str(uuid.uuid4())]}
        serializer = BulkDeleteSerializer(data=data)
        assert not serializer.is_valid()

    def test_invalid_neither_select_all_nor_ids(self):
        """Test that providing neither select_all nor ids fails."""
        serializer = BulkDeleteSerializer(data={})
        assert not serializer.is_valid()

    def test_invalid_empty_ids_without_select_all(self):
        """Test that empty ids list without select_all fails."""
        data = {"ids": []}
        serializer = BulkDeleteSerializer(data=data)
        assert not serializer.is_valid()

    def test_invalid_exclude_ids_without_select_all(self):
        """Test that exclude_ids without select_all fails."""
        data = {"ids": [str(uuid.uuid4())], "exclude_ids": [str(uuid.uuid4())]}
        serializer = BulkDeleteSerializer(data=data)
        assert not serializer.is_valid()

    def test_invalid_uuid_in_ids(self):
        """Test that invalid UUID in ids fails."""
        data = {"ids": ["not-a-uuid"]}
        serializer = BulkDeleteSerializer(data=data)
        assert not serializer.is_valid()

    def test_invalid_uuid_in_exclude_ids(self):
        """Test that invalid UUID in exclude_ids fails."""
        data = {"select_all": True, "exclude_ids": ["not-a-uuid"]}
        serializer = BulkDeleteSerializer(data=data)
        assert not serializer.is_valid()

    def test_defaults_applied(self):
        """Test that defaults are correctly applied."""
        data = {"select_all": True}
        serializer = BulkDeleteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["ids"] == []
        assert serializer.validated_data["exclude_ids"] == []

    def test_select_all_false_is_default(self):
        """Test that select_all defaults to False."""
        data = {"ids": [str(uuid.uuid4())]}
        serializer = BulkDeleteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["select_all"] is False

from rest_framework import serializers

from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.serializers.graph_version import (
    GraphVersionDetailSerializer,
    GraphVersionListSerializer,
    prefetch_version_detail,
)
from tfc.temporal.agent_playground.types import MAX_CONCURRENT_NODES_ERROR


class UserBriefSerializer(serializers.Serializer):
    """Brief user info serializer."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)


def _get_latest_version(graph):
    """Get the latest version (highest version_number) for a graph."""
    return (
        GraphVersion.no_workspace_objects.filter(graph=graph)
        .order_by("-version_number")
        .first()
    )


class GraphListSerializer(serializers.ModelSerializer):
    """Serializer for listing graphs with lightweight fields only.

    Expects the queryset to be annotated with _active_version_id,
    _active_version_number, and _node_count (via _annotate_graph_list_fields).
    """

    created_by = UserBriefSerializer(read_only=True)
    collaborators = UserBriefSerializer(many=True, read_only=True)
    active_version_id = serializers.UUIDField(
        source="_active_version_id", read_only=True, default=None
    )
    active_version_number = serializers.IntegerField(
        source="_active_version_number", read_only=True, default=None
    )
    node_count = serializers.IntegerField(
        source="_node_count", read_only=True, default=None
    )

    class Meta:
        model = Graph
        fields = [
            "id",
            "name",
            "description",
            "is_template",
            "max_concurrent_nodes",
            "created_at",
            "updated_at",
            "created_by",
            "collaborators",
            "active_version_id",
            "active_version_number",
            "node_count",
        ]
        read_only_fields = fields


class GraphCreateResponseSerializer(serializers.ModelSerializer):
    """Serializer for graph create response (with full version details including nodes/edges)."""

    created_by = UserBriefSerializer(read_only=True)
    collaborators = UserBriefSerializer(many=True, read_only=True)
    active_version = serializers.SerializerMethodField()

    class Meta:
        model = Graph
        fields = [
            "id",
            "name",
            "description",
            "is_template",
            "max_concurrent_nodes",
            "created_at",
            "updated_at",
            "created_by",
            "collaborators",
            "active_version",
        ]
        read_only_fields = fields

    def get_active_version(self, obj):
        """Get the current version with full nested structure (including nodes/edges)."""
        version = _get_latest_version(obj)
        if version:
            version = prefetch_version_detail(version)
            return GraphVersionDetailSerializer(version).data
        return None


class GraphDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer for graphs with current version expanded."""

    active_version = serializers.SerializerMethodField()

    class Meta:
        model = Graph
        fields = [
            "id",
            "name",
            "description",
            "is_template",
            "max_concurrent_nodes",
            "created_at",
            "updated_at",
            "active_version",
        ]

        read_only_fields = fields

    def get_active_version(self, obj):
        """Get the latest version (highest version_number) with full nested structure."""
        version = _get_latest_version(obj)
        if version:
            version = prefetch_version_detail(version)
            return GraphVersionDetailSerializer(version).data
        return None


class GraphCreateSerializer(serializers.Serializer):
    """Serializer for creating a new graph."""

    name = serializers.CharField(required=True, max_length=255)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None
    )
    max_concurrent_nodes = serializers.IntegerField(
        required=False,
        min_value=1,
        error_messages={
            "min_value": MAX_CONCURRENT_NODES_ERROR,
            "invalid": MAX_CONCURRENT_NODES_ERROR,
        },
    )


class GraphUpdateSerializer(serializers.Serializer):
    """Serializer for updating graph metadata."""

    name = serializers.CharField(required=False, max_length=255)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    max_concurrent_nodes = serializers.IntegerField(
        required=False,
        min_value=1,
        error_messages={
            "min_value": MAX_CONCURRENT_NODES_ERROR,
            "invalid": MAX_CONCURRENT_NODES_ERROR,
        },
    )


class BulkDeleteSerializer(serializers.Serializer):
    """Serializer for bulk-deleting graphs by ID list or select_all."""

    ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=[],
        help_text="List of graph UUIDs to delete",
    )
    select_all = serializers.BooleanField(required=False, default=False)
    exclude_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=[],
        help_text="Graph UUIDs to exclude when using select_all",
    )

    def validate(self, attrs):
        select_all = attrs.get("select_all", False)
        ids = attrs.get("ids", [])
        exclude_ids = attrs.get("exclude_ids", [])

        if select_all and ids:
            raise serializers.ValidationError(
                "Cannot provide both 'select_all' and 'ids'."
            )

        if not select_all and not ids:
            raise serializers.ValidationError(
                "A list of IDs or select_all flag is required for deletion."
            )

        if not select_all and exclude_ids:
            raise serializers.ValidationError(
                "'exclude_ids' can only be used with 'select_all'."
            )

        return attrs


class ExposedPortSerializer(serializers.Serializer):
    """Serializer for an exposed (unconnected) port of a referenceable graph."""

    id = serializers.UUIDField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    direction = serializers.CharField(read_only=True)
    data_schema = serializers.JSONField(read_only=True)
    required = serializers.BooleanField(read_only=True)
    default_value = serializers.JSONField(read_only=True, allow_null=True)
    metadata = serializers.JSONField(read_only=True)


class GraphVersionInfoSerializer(serializers.Serializer):
    """Serializer for version information in referenceable graphs."""

    id = serializers.UUIDField(read_only=True)
    version_number = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    exposed_ports = ExposedPortSerializer(many=True, read_only=True)


class ReferenceableGraphSerializer(serializers.Serializer):
    """Serializer for graphs that can be referenced as subgraphs.

    Returns all non-draft versions (active + inactive) with their exposed ports.
    """

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_null=True)
    is_template = serializers.BooleanField(read_only=True)
    versions = GraphVersionInfoSerializer(many=True, read_only=True)

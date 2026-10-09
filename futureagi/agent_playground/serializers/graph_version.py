from django.db.models import Prefetch
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from agent_playground.models.choices import GraphVersionStatus
from agent_playground.models.edge import Edge
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.port import Port
from agent_playground.serializers.node import NodeReadSerializer, NodeWriteSerializer
from agent_playground.serializers.node_connection import (
    NodeConnectionReadSerializer,
    NodeConnectionWriteSerializer,
)
from tfc.utils.serializer_fields import JsonValueField


def prefetch_version_detail(version: GraphVersion) -> GraphVersion:
    """Prefetch nested relations for a single GraphVersion to avoid N+1 queries."""
    if version is None:
        return None
    prefetched = (
        GraphVersion.no_workspace_objects.filter(pk=version.pk)
        .prefetch_related(
            Prefetch(
                "nodes",
                queryset=Node.no_workspace_objects.select_related(
                    "node_template",
                    "ref_graph_version__graph",
                    "prompt_template_node__prompt_template",
                    "prompt_template_node__prompt_version",
                ).prefetch_related(
                    Prefetch(
                        "ports",
                        queryset=Port.no_workspace_objects.select_related(
                            "ref_port"
                        ).prefetch_related(
                            Prefetch(
                                "incoming_edges",
                                queryset=Edge.no_workspace_objects.select_related(
                                    "source_port__node"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            "node_connections__source_node",
            "node_connections__target_node",
        )
        .first()
    )
    return prefetched or version


class GraphVersionListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing graph versions."""

    global_variables = serializers.SerializerMethodField()
    tags = JsonValueField(read_only=True)
    # The version writers accept "" and null, so both can be read back.
    commit_message = serializers.CharField(
        read_only=True, allow_null=True, allow_blank=True
    )

    class Meta:
        model = GraphVersion
        fields = [
            "id",
            "version_number",
            "status",
            "tags",
            "commit_message",
            "created_at",
            "global_variables",
        ]
        read_only_fields = fields
        swagger_schema_fields = {"required": fields}

    @swagger_serializer_method(
        serializer_or_field=serializers.ListField(child=serializers.CharField())
    )
    def get_global_variables(self, obj):
        return self.context.get("global_variables_map", {}).get(obj.id, [])


class GraphVersionDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer for graph versions with nested nodes."""

    nodes = NodeReadSerializer(many=True, read_only=True)
    node_connections = NodeConnectionReadSerializer(many=True, read_only=True)
    tags = JsonValueField(read_only=True)
    commit_message = serializers.CharField(
        read_only=True, allow_null=True, allow_blank=True
    )

    class Meta:
        model = GraphVersion
        fields = [
            "id",
            "version_number",
            "status",
            "tags",
            "commit_message",
            "created_at",
            "nodes",
            "node_connections",
        ]
        read_only_fields = fields
        swagger_schema_fields = {"required": fields}


def _validate_no_duplicate_edges(edges):
    """Raise ValidationError if edges list contains duplicate connections."""
    seen = set()
    for edge in edges:
        pair = (edge["source_port_temp_id"], edge["target_port_temp_id"])
        if pair in seen:
            raise serializers.ValidationError(
                {"edges": f"Duplicate edge from '{pair[0]}' to '{pair[1]}'"}
            )
        seen.add(pair)


def _validate_no_duplicate_node_connections(node_connections):
    """Raise ValidationError if node_connections list contains duplicate connections."""
    seen = set()
    for nc in node_connections:
        pair = (nc["source_node_id"], nc["target_node_id"])
        if pair in seen:
            raise serializers.ValidationError(
                {
                    "node_connections": f"Duplicate node connection from '{pair[0]}' to '{pair[1]}'"
                }
            )
        seen.add(pair)


def _validate_no_duplicate_port_keys(nodes):
    """Raise ValidationError if any node has duplicate port keys or display names."""
    for node in nodes:
        seen_keys = set()
        seen_display_names = set()
        for port in node.get("ports", []):
            key = port["key"]
            display_name = port["display_name"]
            # DB constraint excludes key="custom" from uniqueness
            if key != "custom" and key in seen_keys:
                raise serializers.ValidationError(
                    {"nodes": f"Duplicate port key '{key}' on node '{node['name']}'"}
                )
            seen_keys.add(key)
            if display_name in seen_display_names:
                raise serializers.ValidationError(
                    {
                        "nodes": f"Duplicate port display name '{display_name}'"
                        f" on node '{node['name']}'"
                    }
                )
            seen_display_names.add(display_name)


class VersionCreateSerializer(serializers.Serializer):
    """Serializer for creating a new version with optional nodes and node connections.

    FE sends nodes + node_connections. Backend auto-creates ports and edges.
    """

    status = serializers.ChoiceField(
        required=False,
        choices=[GraphVersionStatus.DRAFT, GraphVersionStatus.ACTIVE],
        default=GraphVersionStatus.DRAFT,
    )
    commit_message = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )
    nodes = NodeWriteSerializer(many=True, required=False, default=list)
    node_connections = NodeConnectionWriteSerializer(
        many=True, required=False, default=list
    )

    def validate(self, attrs):
        _validate_no_duplicate_node_connections(attrs.get("node_connections", []))
        _validate_no_duplicate_port_keys(attrs.get("nodes", []))
        return attrs


class VersionMetadataUpdateSerializer(serializers.Serializer):
    """Serializer for PATCH version metadata (status promotion + commit_message)."""

    status = serializers.ChoiceField(
        required=False,
        choices=[GraphVersionStatus.DRAFT, GraphVersionStatus.ACTIVE],
        default=GraphVersionStatus.DRAFT,
    )
    commit_message = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )

from rest_framework import serializers

from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.node_execution import NodeExecution
from tfc.utils.api_serializers import PaginationMetadataSerializer


class NodeExecutionBriefSerializer(serializers.ModelSerializer):
    """Brief node execution status to attach to each node."""

    class Meta:
        model = NodeExecution
        fields = [
            "id",
            "status",
            "started_at",
            "completed_at",
            "error_message",
        ]
        read_only_fields = fields


class GraphExecutionListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing graph executions."""

    class Meta:
        model = GraphExecution
        fields = [
            "id",
            "status",
            "started_at",
            "completed_at",
            "graph_version",
            "created_at",
        ]
        read_only_fields = fields


class GraphExecutionSerializer(serializers.ModelSerializer):
    """Basic graph execution details (without nested detail data)."""

    class Meta:
        model = GraphExecution
        fields = [
            "id",
            "status",
            "input_payload",
            "output_payload",
            "started_at",
            "completed_at",
            "error_message",
        ]
        read_only_fields = fields


class GraphExecutionListResultSerializer(serializers.Serializer):
    executions = GraphExecutionListSerializer(many=True, read_only=True)
    metadata = PaginationMetadataSerializer(read_only=True)


class GraphExecutionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = GraphExecutionListResultSerializer()


class GraphExecutionDetailResultSerializer(GraphExecutionSerializer):
    nodes = serializers.ListField(child=serializers.JSONField(), read_only=True)
    node_connections = serializers.ListField(
        child=serializers.JSONField(), read_only=True
    )

    class Meta(GraphExecutionSerializer.Meta):
        fields = [*GraphExecutionSerializer.Meta.fields, "nodes", "node_connections"]
        read_only_fields = fields


class GraphExecutionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = GraphExecutionDetailResultSerializer()


class NodeExecutionDataSerializer(serializers.Serializer):
    port_id = serializers.UUIDField(read_only=True)
    port_key = serializers.CharField(read_only=True)
    port_direction = serializers.CharField(read_only=True)
    payload = serializers.JSONField(read_only=True, allow_null=True)
    is_valid = serializers.BooleanField(read_only=True)
    validation_errors = serializers.JSONField(read_only=True, allow_null=True)


class NodeExecutionDetailResultSerializer(serializers.Serializer):
    node_execution_id = serializers.UUIDField(read_only=True)
    node_id = serializers.UUIDField(read_only=True)
    node_name = serializers.CharField(read_only=True)
    node_type = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    started_at = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)
    inputs = NodeExecutionDataSerializer(many=True, read_only=True)
    outputs = NodeExecutionDataSerializer(many=True, read_only=True)


class NodeExecutionDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = NodeExecutionDetailResultSerializer()

from rest_framework import serializers

from agent_playground.models.node_connection import NodeConnection


class NodeConnectionReadSerializer(serializers.ModelSerializer):
    """Serializer for reading node connection data in responses."""

    source_node_id = serializers.UUIDField(source="source_node.id", read_only=True)
    target_node_id = serializers.UUIDField(source="target_node.id", read_only=True)

    class Meta:
        model = NodeConnection
        fields = [
            "id",
            "source_node_id",
            "target_node_id",
        ]
        read_only_fields = fields
        swagger_schema_fields = {"required": fields}


class NodeConnectionWriteSerializer(serializers.Serializer):
    """Serializer for writing node connection data in requests."""

    source_node_id = serializers.UUIDField(
        required=True, help_text="UUID of the source node"
    )
    target_node_id = serializers.UUIDField(
        required=True, help_text="UUID of the target node"
    )


class CreateNodeConnectionSerializer(serializers.Serializer):
    """Serializer for POST /node-connections/."""

    id = serializers.UUIDField(required=True, help_text="FE-generated UUID")
    source_node_id = serializers.UUIDField(required=True)
    target_node_id = serializers.UUIDField(required=True)

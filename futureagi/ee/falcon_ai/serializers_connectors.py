from rest_framework import serializers

from ee.falcon_ai.models import MCPConnector


class MCPConnectorListSerializer(serializers.ModelSerializer):
    tool_count = serializers.SerializerMethodField()

    class Meta:
        model = MCPConnector
        fields = [
            "id",
            "name",
            "server_url",
            "transport",
            "auth_type",
            "is_active",
            "is_verified",
            "tool_count",
            "last_discovery_at",
            "last_error",
            "created_at",
        ]

    def get_tool_count(self, obj):
        return len(obj.enabled_tool_names or [])


class MCPConnectorDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = MCPConnector
        fields = [
            "id",
            "name",
            "server_url",
            "transport",
            "auth_type",
            "auth_header_name",
            "is_active",
            "is_verified",
            "discovered_tools",
            "enabled_tool_names",
            "last_discovery_at",
            "last_error",
            "created_at",
            "updated_at",
        ]


class MCPConnectorCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    server_url = serializers.URLField()
    transport = serializers.ChoiceField(
        choices=["sse", "streamable_http"], default="streamable_http"
    )
    auth_type = serializers.ChoiceField(
        choices=["none", "api_key", "bearer", "oauth"], default="none"
    )
    auth_header_name = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default="Authorization"
    )
    auth_header_value = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class MCPConnectorToolsSerializer(serializers.Serializer):
    enabled_tool_names = serializers.ListField(child=serializers.CharField())

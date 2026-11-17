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
    # The client ID is a public identifier. The secret is never returned, only
    # whether one is saved.
    has_oauth_client_secret = serializers.SerializerMethodField()

    class Meta:
        model = MCPConnector
        fields = [
            "id",
            "name",
            "server_url",
            "transport",
            "auth_type",
            "auth_header_name",
            "oauth_client_id",
            "has_oauth_client_secret",
            "is_active",
            "is_verified",
            "discovered_tools",
            "enabled_tool_names",
            "last_discovery_at",
            "last_error",
            "created_at",
            "updated_at",
        ]

    def get_has_oauth_client_secret(self, obj):
        return bool(obj.oauth_client_secret)


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
        required=False, allow_blank=True, default="", write_only=True
    )
    oauth_client_id = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )
    oauth_client_secret = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
        write_only=True,
    )

    def validate(self, attrs):
        # A secret belongs to the client the server issued it for.
        if attrs.get("oauth_client_secret") and not attrs.get("oauth_client_id"):
            raise serializers.ValidationError(
                {"oauth_client_secret": "Enter the OAuth client ID for this secret."}
            )
        return attrs


class MCPConnectorToolsSerializer(serializers.Serializer):
    enabled_tool_names = serializers.ListField(child=serializers.CharField())

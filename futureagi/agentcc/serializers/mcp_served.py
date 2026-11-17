"""Public contract of the runtime-backed, org-admin MCP inventory read."""

from rest_framework import serializers


class MCPServedQuerySerializer(serializers.Serializer):
    vs = serializers.CharField(required=False, allow_blank=True, max_length=256)
    key_id = serializers.CharField(required=False, allow_blank=True, max_length=256)
    server_id = serializers.CharField(required=False, allow_blank=True, max_length=256)


class MCPServedIdentitySerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=("composio", "pipedream", "other"))
    toolkit_slug = serializers.CharField(allow_blank=True, max_length=64)
    toolkit_name = serializers.CharField(allow_blank=True, max_length=96)
    source = serializers.ChoiceField(
        choices=("from server configuration",), required=False
    )


class MCPServedToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    server_id = serializers.CharField()
    upstream_name = serializers.CharField()
    revision = serializers.CharField(required=False, allow_blank=True)
    quarantined = serializers.BooleanField()
    identity = MCPServedIdentitySerializer()


class MCPServedResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPServedToolSerializer(many=True)

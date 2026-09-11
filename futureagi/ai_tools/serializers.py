from rest_framework import serializers


class ToolParameterSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    type = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    required = serializers.BooleanField(read_only=True)


class ToolDiscoveryItemSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True, allow_blank=True)
    parameters = ToolParameterSerializer(many=True, read_only=True)
    returns = serializers.JSONField(read_only=True, required=False, allow_null=True)
    metadata = serializers.JSONField(read_only=True, required=False, allow_null=True)


class ToolDiscoveryResultSerializer(serializers.Serializer):
    tools = ToolDiscoveryItemSerializer(many=True, read_only=True)
    categories = serializers.ListField(child=serializers.CharField(), read_only=True)
    total = serializers.IntegerField(read_only=True)


class ToolDiscoveryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ToolDiscoveryResultSerializer()

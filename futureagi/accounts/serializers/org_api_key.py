from rest_framework import serializers


class UserSecretKeySerializer(serializers.Serializer):
    key_id = serializers.UUIDField()


class CreateSecretKeySerializer(serializers.Serializer):
    key_name = serializers.CharField(max_length=100, required=True)
    workspace_id = serializers.UUIDField(required=False, allow_null=True)

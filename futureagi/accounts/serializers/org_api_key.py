from rest_framework import serializers

from accounts.models import OrgApiKey


class OrgApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgApiKey
        fields = [
            "id",
            "api_key",
            "secret_key",
        ]


class UserSecretKeySerializer(serializers.Serializer):
    key_id = serializers.UUIDField()


class CreateSecretKeySerializer(serializers.Serializer):
    key_name = serializers.CharField(max_length=100, required=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

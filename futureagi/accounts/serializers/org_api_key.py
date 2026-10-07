from django.utils import timezone
from rest_framework import serializers

from accounts.models import OrgApiKey
from tfc.utils.error_codes import get_error_message


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
    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Optional expiry. Omit or send null for a key that never expires.",
    )

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError(
                get_error_message("API_KEY_EXPIRY_IN_PAST")
            )
        return value

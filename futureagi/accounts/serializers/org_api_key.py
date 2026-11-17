from django.utils import timezone
from rest_framework import serializers

from tfc.utils.error_codes import get_error_message


class UserSecretKeySerializer(serializers.Serializer):
    key_id = serializers.UUIDField()


class CreateSecretKeySerializer(serializers.Serializer):
    key_name = serializers.CharField(max_length=100, required=True)
    workspace_id = serializers.UUIDField(required=False, allow_null=True)
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

    # A ``coding_agent`` key may make only the calls of a coding agent's fix loop,
    # on the one simulation environment it names.
    type = serializers.ChoiceField(
        choices=["user", "coding_agent"], default="user", required=False
    )
    simulation_environment_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if (attrs["type"] == "coding_agent") != ("simulation_environment_id" in attrs):
            raise serializers.ValidationError(
                {
                    "simulation_environment_id": (
                        "Required for a coding_agent key, and accepted for no other."
                    )
                }
            )
        if attrs["type"] == "coding_agent" and "workspace_id" in attrs:
            raise serializers.ValidationError(
                {"workspace_id": "A coding agent key uses its environment's workspace; omit workspace_id."}
            )
        return attrs

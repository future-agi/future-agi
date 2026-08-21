from rest_framework import serializers


class SecretReferenceSerializer(serializers.Serializer):
    manager = serializers.CharField(max_length=64)
    key = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=255, required=False, allow_null=True)
    purpose = serializers.CharField(max_length=128)


class HarnessJobCreateSerializer(serializers.Serializer):
    source_path = serializers.CharField(max_length=4096)
    scenario_count = serializers.IntegerField(default=10, min_value=1, max_value=100)
    seed = serializers.IntegerField(required=False, allow_null=True)
    agent_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    connector = serializers.CharField(max_length=128, default="auto")
    connector_config = serializers.JSONField(default=dict)
    secret_refs = serializers.DictField(
        child=SecretReferenceSerializer(), required=False, default=dict
    )
    metadata = serializers.JSONField(default=dict)

    def validate_connector_config(self, value):
        forbidden = {"token", "secret", "password", "api_key", "api_secret"}
        present = forbidden & {str(key).lower() for key in value}
        if present:
            raise serializers.ValidationError(
                "credentials must be supplied as secret_refs, never as values"
            )
        return value


class HarnessJobActionSerializer(serializers.Serializer):
    """Optional audit context for job actions."""

    reason = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        help_text="Optional operator-provided reason for the action.",
    )

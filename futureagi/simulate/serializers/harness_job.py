from rest_framework import serializers


class SecretReferenceSerializer(serializers.Serializer):
    manager = serializers.CharField(max_length=64)
    key = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=255, required=False, allow_null=True)
    purpose = serializers.CharField(max_length=128)


class HarnessSourceSerializer(serializers.Serializer):
    source_path = serializers.CharField(max_length=4096, required=False)
    github_repository = serializers.CharField(max_length=512, required=False)
    github_ref = serializers.CharField(max_length=255, required=False)
    github_commit_sha = serializers.RegexField(r"^[0-9a-fA-F]{40}$", required=False)
    github_visibility = serializers.ChoiceField(
        choices=("public", "private"), default="public"
    )
    github_installation_id = serializers.CharField(max_length=255, required=False)
    secret_refs = serializers.DictField(
        child=SecretReferenceSerializer(), required=False, default=dict
    )
    connector_config = serializers.JSONField(required=False, default=dict)

    def validate_connector_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("connector_config must be an object")
        sensitive = ("token", "secret", "password", "api_key", "private_key")
        invalid = [
            str(key)
            for key, item in value.items()
            if any(marker in str(key).lower() for marker in sensitive)
            or not isinstance(item, (str, int, float, bool))
        ]
        if invalid:
            raise serializers.ValidationError(
                "connector configuration must contain scalar non-secret values; "
                "credentials belong in secret_refs"
            )
        return value

    def validate(self, attrs):
        if bool(attrs.get("source_path")) == bool(attrs.get("github_repository")):
            raise serializers.ValidationError(
                "provide exactly one of source_path or github_repository"
            )
        if (
            attrs.get("github_repository")
            and attrs.get("github_visibility") == "private"
            and not attrs.get("github_installation_id")
        ):
            raise serializers.ValidationError(
                {
                    "github_installation_id": "install/select the GitHub App for a private repository"
                }
            )
        return attrs


class HarnessPreflightSerializer(HarnessSourceSerializer):
    pass


class HarnessJobCreateSerializer(HarnessSourceSerializer):
    scenario_count = serializers.IntegerField(default=10, min_value=1, max_value=100)
    seed = serializers.IntegerField(required=False, allow_null=True)
    agent_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    connector = serializers.CharField(max_length=128, default="auto")
    metadata = serializers.JSONField(default=dict)


class HarnessJobActionSerializer(serializers.Serializer):
    """Optional audit context for job actions."""

    reason = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        help_text="Optional operator-provided reason for the action.",
    )

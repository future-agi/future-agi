import re

from rest_framework import serializers


RUNNER_RESERVED_ENVIRONMENT = {
    "DOCKER_HOST",
    "FI_API_KEY",
    "FI_BASE_URL",
    "FI_SECRET_KEY",
    "HARNESS_PLATFORM_API_KEY",
    "HARNESS_PLATFORM_SECRET_KEY",
    "HARNESS_PLATFORM_URL",
    "HOME",
    "PATH",
    "PYTHONPATH",
}


class SecretReferenceSerializer(serializers.Serializer):
    manager = serializers.CharField(max_length=64)
    key = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=255, required=False, allow_null=True)
    purpose = serializers.CharField(max_length=128)


class HarnessSourceSerializer(serializers.Serializer):
    source_path = serializers.CharField(max_length=4096, required=False)
    source_id = serializers.RegexField(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        required=False,
    )
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
    environment_values = serializers.DictField(
        child=serializers.CharField(
            max_length=65536, allow_blank=True, trim_whitespace=False
        ),
        required=False,
        default=dict,
        write_only=True,
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

    def validate_environment_values(self, value):
        if len(value) > 256:
            raise serializers.ValidationError("at most 256 environment values are allowed")
        invalid = [
            str(name)
            for name, item in value.items()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name))
            or "\x00" in item
            or str(name) in RUNNER_RESERVED_ENVIRONMENT
            or str(name).startswith("ALK_")
        ]
        if invalid:
            raise serializers.ValidationError(
                f"invalid environment variable names or values: {', '.join(invalid)}"
            )
        total = sum(
            len(str(name).encode()) + len(item.encode())
            for name, item in value.items()
        )
        if total > 262_144:
            raise serializers.ValidationError(
                "combined environment values may not exceed 256 KiB"
            )
        return value

    def validate(self, attrs):
        sources = (
            attrs.get("source_path"),
            attrs.get("source_id"),
            attrs.get("github_repository"),
        )
        if sum(bool(value) for value in sources) != 1:
            raise serializers.ValidationError(
                "provide exactly one of source_id, source_path or github_repository"
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
        if set(attrs.get("environment_values", {})) & set(attrs.get("secret_refs", {})):
            raise serializers.ValidationError(
                "an environment variable cannot be both uploaded and a secret reference"
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


class HarnessSourceUploadResponseSerializer(serializers.Serializer):
    source_id = serializers.UUIDField()
    name = serializers.CharField()
    file_count = serializers.IntegerField()
    total_bytes = serializers.IntegerField()

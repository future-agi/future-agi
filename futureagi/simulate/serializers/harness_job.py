from __future__ import annotations

from rest_framework import serializers


class SecretReferenceSerializer(serializers.Serializer):
    manager = serializers.ChoiceField(choices=("platform-vault",))
    key = serializers.CharField(max_length=255)
    version = serializers.CharField(max_length=255, required=False, allow_null=True)
    purpose = serializers.ChoiceField(choices=("target_provider", "source_checkout"))


class HarnessSourceSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("github", "archive", "remote"))
    repository = serializers.RegexField(
        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", required=False, allow_null=True
    )
    ref = serializers.RegexField(r"^[A-Za-z0-9._/-]+$", required=False, allow_null=True)
    commit_sha = serializers.RegexField(
        r"^[0-9a-fA-F]{40}$", required=False, allow_null=True
    )
    installation_id = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )
    archive_artifact_id = serializers.UUIDField(required=False, allow_null=True)
    endpoint = serializers.URLField(required=False, allow_null=True)
    visibility = serializers.ChoiceField(
        choices=("public", "private"), default="public"
    )

    def validate(self, attrs):
        kind = attrs["kind"]
        if kind == "github":
            if not attrs.get("repository"):
                raise serializers.ValidationError(
                    {"repository": "required for github sources"}
                )
            if attrs["visibility"] == "private" and not attrs.get("installation_id"):
                raise serializers.ValidationError(
                    {"installation_id": "required for private github sources"}
                )
        elif kind == "archive" and not attrs.get("archive_artifact_id"):
            raise serializers.ValidationError(
                {"archive_artifact_id": "required for archive sources"}
            )
        elif kind == "remote" and not attrs.get("endpoint"):
            raise serializers.ValidationError(
                {"endpoint": "required for remote sources"}
            )
        return attrs


class HarnessAgentSerializer(serializers.Serializer):
    connector = serializers.ChoiceField(choices=("livekit", "vapi", "retell", "auto"))
    config = serializers.DictField(default=dict)
    secret_refs = serializers.DictField(
        child=SecretReferenceSerializer(), required=False, default=dict
    )

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be an object")
        secret_names = ("token", "secret", "password", "api_key", "private_key")
        invalid = [
            str(key)
            for key, item in value.items()
            if any(marker in str(key).lower() for marker in secret_names)
            or not isinstance(item, (str, int, float, bool))
        ]
        if invalid:
            raise serializers.ValidationError(
                "config must contain scalar non-secret values; use secret_refs"
            )
        return value

    def validate_secret_refs(self, value):
        for alias, reference in value.items():
            if not alias or not alias.replace("_", "").isalnum():
                raise serializers.ValidationError(
                    f"invalid environment-variable alias: {alias!r}"
                )
            if reference["purpose"] != "target_provider":
                raise serializers.ValidationError(
                    "agent secret_refs only accept purpose target_provider"
                )
        return value


class HarnessRuntimeSerializer(serializers.Serializer):
    isolation = serializers.ChoiceField(
        choices=("dedicated_vm",), default="dedicated_vm"
    )
    cpu_units = serializers.IntegerField(default=4, min_value=1)
    memory_mb = serializers.IntegerField(default=8192, min_value=1024)
    parallelism = serializers.IntegerField(default=1, min_value=1, max_value=8)
    concurrency_weight = serializers.IntegerField(default=1, min_value=1, max_value=10)
    max_duration_seconds = serializers.IntegerField(
        default=3600, min_value=60, max_value=86400
    )
    network_policy = serializers.ChoiceField(choices=("live",), default="live")


class HarnessSecuritySerializer(serializers.Serializer):
    untrusted_source = serializers.BooleanField(default=True)
    read_only_source = serializers.BooleanField(default=True)
    allow_privileged = serializers.BooleanField(default=False)
    allow_host_runtime_control = serializers.BooleanField(default=False)
    allowed_egress_domains = serializers.ListField(
        child=serializers.RegexField(r"^(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+$"),
        default=list,
    )

    def validate(self, attrs):
        if (
            not attrs["untrusted_source"]
            or not attrs["read_only_source"]
            or attrs["allow_privileged"]
            or attrs["allow_host_runtime_control"]
        ):
            raise serializers.ValidationError(
                "hosted security invariants cannot be relaxed"
            )
        return attrs


class HarnessRetrySerializer(serializers.Serializer):
    max_infrastructure_attempts = serializers.IntegerField(
        default=2, min_value=1, max_value=5
    )
    initial_backoff_seconds = serializers.FloatField(
        default=1, min_value=0, max_value=60
    )
    max_backoff_seconds = serializers.FloatField(default=15, min_value=0, max_value=300)
    retryable_domains = serializers.ListField(
        child=serializers.ChoiceField(
            choices=("infrastructure", "connectivity", "platform_sync")
        ),
        default=lambda: ["infrastructure", "connectivity"],
    )

    def validate(self, attrs):
        if attrs["max_backoff_seconds"] < attrs["initial_backoff_seconds"]:
            raise serializers.ValidationError(
                "max_backoff_seconds must be >= initial_backoff_seconds"
            )
        return attrs


class HarnessArtifactSerializer(serializers.Serializer):
    level = serializers.ChoiceField(
        choices=("metadata-only", "traces", "traces-and-recordings", "full")
    )
    retention_days = serializers.IntegerField(default=30, min_value=1, max_value=3650)
    allow_bundle_download = serializers.BooleanField(default=False)
    max_artifact_bytes = serializers.IntegerField(default=1_073_741_824, min_value=0)


class HarnessJobCreateSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(
        choices=("futureagi.harness-job.v1",),
        default="futureagi.harness-job.v1",
    )
    run_id = serializers.UUIDField(required=False)
    source = HarnessSourceSerializer()
    agent = HarnessAgentSerializer()
    scenario_count = serializers.IntegerField(default=10, min_value=1, max_value=10)
    seed = serializers.IntegerField(required=False, allow_null=True)
    runtime = HarnessRuntimeSerializer(default=dict)
    security = HarnessSecuritySerializer(default=dict)
    retry = HarnessRetrySerializer(default=dict)
    artifacts = HarnessArtifactSerializer()
    platform_run_id = serializers.CharField(
        max_length=255, required=False, allow_null=True
    )
    metadata = serializers.DictField(default=dict)

    def validate(self, attrs):
        runtime = attrs["runtime"]
        connector = attrs["agent"]["connector"]
        if connector in {"livekit", "vapi", "retell", "auto"} and (
            runtime["parallelism"] > runtime["cpu_units"]
        ):
            raise serializers.ValidationError(
                {"runtime": "voice parallelism must not exceed cpu_units"}
            )
        if attrs["source"]["kind"] == "remote" and attrs["agent"]["secret_refs"]:
            raise serializers.ValidationError(
                {"agent": "remote sources must own their target credentials"}
            )
        return attrs


class HarnessJobActionSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(
        choices=("user_canceled", "ttl_exceeded"), default="user_canceled"
    )


class HarnessPreflightSerializer(HarnessJobCreateSerializer):
    pass

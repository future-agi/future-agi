from rest_framework import serializers

from simulate.serializers.harness_job import SUBFOLDER_PATTERN, check_credential_values
from tfc.utils.api_serializers import StrictInputSerializer


class AgentSourceRequestSerializer(StrictInputSerializer):
    """A source to find agents in: a GitHub repository at a branch, or an upload."""

    kind = serializers.ChoiceField(choices=("github", "archive"))
    repository = serializers.RegexField(
        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", required=False
    )
    ref = serializers.RegexField(r"^[A-Za-z0-9._/-]+$", required=False)
    visibility = serializers.ChoiceField(
        choices=("public", "private"), default="public"
    )
    installation_id = serializers.CharField(max_length=255, required=False)
    repository_id = serializers.IntegerField(min_value=1, required=False)
    archive_artifact_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs["kind"] == "github":
            if not attrs.get("repository"):
                raise serializers.ValidationError(
                    {"repository": "required for a github source"}
                )
            if ".." in str(attrs.get("ref") or ""):
                raise serializers.ValidationError({"ref": "not a branch or tag"})
            if attrs["visibility"] == "private" and not attrs.get("installation_id"):
                raise serializers.ValidationError(
                    {"installation_id": "required for a private repository"}
                )
            if attrs.get("archive_artifact_id"):
                raise serializers.ValidationError(
                    {"archive_artifact_id": "not part of a github source"}
                )
        elif not attrs.get("archive_artifact_id"):
            raise serializers.ValidationError(
                {"archive_artifact_id": "required for an upload"}
            )
        if attrs.get("archive_artifact_id"):
            attrs["archive_artifact_id"] = str(attrs["archive_artifact_id"])
        return attrs


class AgentConnectRequestSerializer(AgentSourceRequestSerializer):
    """The agent picked from a source: its folder, the name it gets, and the
    environment values its code reads (names and values, kept as its credentials)."""

    name = serializers.CharField(max_length=255)
    subfolder = serializers.RegexField(
        SUBFOLDER_PATTERN, max_length=512, required=False, allow_blank=True
    )
    env_values = serializers.DictField(
        child=serializers.CharField(max_length=65_536, trim_whitespace=False),
        required=False,
    )

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("name the agent")
        return value.strip()

    def validate_env_values(self, value):
        problems = check_credential_values(value)
        if problems:
            raise serializers.ValidationError(problems)
        return {name: text for name, text in value.items() if text}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("subfolder") and attrs["kind"] != "github":
            raise serializers.ValidationError(
                {"subfolder": "only a github source names a folder"}
            )
        return attrs

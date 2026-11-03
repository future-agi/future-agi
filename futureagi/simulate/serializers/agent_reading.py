import re

from rest_framework import serializers

from simulate.serializers.harness_job import SUBFOLDER_PATTERN

AGENT_SCAN_SCHEMA = "futureagi.agent-scan.v1"
MAX_AGENTS = 50
FRAMEWORK_NAMES = (
    "langchain",
    "langgraph",
    "livekit",
    "claude_agent_sdk",
    "openai_agents",
    "pydantic_ai",
    "google_adk",
    "crewai",
    "pipecat",
    "deepagents",
    "other",
    "none",
    "unknown",
)
_RELATIVE_PATH = re.compile(SUBFOLDER_PATTERN)


def _relative_path(value):
    if value and not _RELATIVE_PATH.fullmatch(value):
        raise serializers.ValidationError("must be a path inside the repository")
    return value


class FrameworkSerializer(serializers.Serializer):
    name = serializers.ChoiceField(choices=FRAMEWORK_NAMES)
    version = serializers.CharField(max_length=64, allow_null=True)
    label = serializers.CharField(max_length=128, allow_null=True)

    def validate(self, attrs):
        if (attrs["name"] == "other") != bool(attrs["label"]):
            raise serializers.ValidationError(
                {"label": "set for 'other' and only for 'other'"}
            )
        return attrs


class EstimatesSerializer(serializers.Serializer):
    tools = serializers.IntegerField(min_value=0)
    prompt_files = serializers.IntegerField(min_value=0)


class FoundAgentSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    subfolder = serializers.CharField(
        max_length=512, allow_blank=True, validators=[_relative_path]
    )
    entry = serializers.CharField(max_length=512, validators=[_relative_path])
    framework = FrameworkSerializer()
    confidence = serializers.ChoiceField(choices=("high", "medium", "low"))
    evidence = serializers.ListField(
        child=serializers.CharField(max_length=200), max_length=5
    )
    env_names = serializers.ListField(
        child=serializers.RegexField(r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128),
        max_length=200,
    )
    estimates = EstimatesSerializer()


class ScanErrorSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^[a-z0-9_]+$", max_length=64)
    message = serializers.CharField(max_length=2000)


class AgentScanSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=(AGENT_SCAN_SCHEMA,))
    status = serializers.ChoiceField(choices=("found", "none", "error"))
    commit_sha = serializers.RegexField(r"^[0-9a-f]{40}$", allow_null=True)
    agents = FoundAgentSerializer(many=True, max_length=MAX_AGENTS)
    truncated = serializers.BooleanField()
    error = ScanErrorSerializer(allow_null=True)

    def validate(self, attrs):
        status, agents, error = attrs["status"], attrs["agents"], attrs["error"]
        if status == "found" and (not agents or error is not None):
            raise serializers.ValidationError(
                "'found' lists at least one agent and no error"
            )
        if status == "none" and (agents or error is not None):
            raise serializers.ValidationError("'none' lists no agent and no error")
        if status == "error" and (agents or error is None):
            raise serializers.ValidationError("'error' lists no agent and an error")
        return attrs

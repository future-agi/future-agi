from rest_framework import serializers


class AgentScanStartedResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)


class FoundAgentFrameworkSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    version = serializers.CharField(read_only=True, allow_null=True)
    label = serializers.CharField(read_only=True, allow_null=True)


class FoundAgentEstimatesSerializer(serializers.Serializer):
    tools = serializers.IntegerField(read_only=True)
    prompt_files = serializers.IntegerField(read_only=True)


class FoundAgentSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    subfolder = serializers.CharField(read_only=True, allow_blank=True)
    entry = serializers.CharField(read_only=True)
    framework = FoundAgentFrameworkSerializer(read_only=True)
    confidence = serializers.ChoiceField(
        choices=("high", "medium", "low"), read_only=True
    )
    evidence = serializers.ListField(child=serializers.CharField(), read_only=True)
    env_names = serializers.ListField(child=serializers.CharField(), read_only=True)
    estimates = FoundAgentEstimatesSerializer(read_only=True)


class AgentScanErrorSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)


class AgentScanFailureSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
    retryable = serializers.BooleanField(read_only=True)


class AgentScanResponseSerializer(serializers.Serializer):
    """A scan as the drawer polls it: pending, running, then the agents found (or
    none), the scan's own error, or the failure that stopped it."""

    id = serializers.UUIDField(read_only=True)
    status = serializers.ChoiceField(
        choices=("pending", "running", "found", "none", "error", "failed"),
        read_only=True,
    )
    kind = serializers.CharField(read_only=True)
    repository = serializers.CharField(read_only=True, allow_null=True)
    ref = serializers.CharField(read_only=True, allow_null=True)
    commit_sha = serializers.CharField(read_only=True, allow_null=True)
    agents = FoundAgentSerializer(many=True, read_only=True)
    truncated = serializers.BooleanField(read_only=True)
    error = AgentScanErrorSerializer(read_only=True, allow_null=True)
    failure = AgentScanFailureSerializer(read_only=True, allow_null=True)


class AgentConnectResponseSerializer(serializers.Serializer):
    agent_id = serializers.UUIDField(read_only=True)
    version_id = serializers.UUIDField(read_only=True)
    exists = serializers.BooleanField(read_only=True)


class AgentFoundCheckResponseSerializer(serializers.Serializer):
    found_checked_at = serializers.DateTimeField(read_only=True)

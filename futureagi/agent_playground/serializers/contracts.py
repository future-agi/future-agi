from rest_framework import serializers


class AgentPlaygroundErrorResponseSerializer(serializers.Serializer):
    """GeneralMethods-style error envelope for Agent Playground APIs."""

    status = serializers.BooleanField(default=False)
    result = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    error = serializers.CharField(required=False, allow_blank=True, allow_null=True)


AGENT_PLAYGROUND_ERROR_RESPONSES = {
    400: AgentPlaygroundErrorResponseSerializer,
    404: AgentPlaygroundErrorResponseSerializer,
    500: AgentPlaygroundErrorResponseSerializer,
}

from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer


class AgentPlaygroundErrorResponseSerializer(ManagementAPIErrorResponseSerializer):
    """GeneralMethods-style error envelope for Agent Playground APIs.

    ``GeneralMethods`` builds every error with ``build_error_envelope``, so the
    body carries the same type/code/detail/attr/details keys as the shared
    management error envelope.
    """


AGENT_PLAYGROUND_ERROR_RESPONSES = {
    400: AgentPlaygroundErrorResponseSerializer,
    404: AgentPlaygroundErrorResponseSerializer,
    500: AgentPlaygroundErrorResponseSerializer,
}

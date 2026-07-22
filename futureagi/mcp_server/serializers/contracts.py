from rest_framework import serializers

from ai_tools.serializers import ToolDiscoveryItemSerializer
from mcp_server.serializers.connection import MCPConnectionSerializer
from mcp_server.serializers.session import MCPSessionSerializer
from mcp_server.serializers.tool_config import MCPToolGroupConfigSerializer
from mcp_server.serializers.usage import (
    MCPUsageSummarySerializer,
    MCPUsageTimelineSerializer,
    MCPUsageToolBreakdownSerializer,
)
from tfc.utils.api_serializers import ApiErrorResponseSerializer


class MCPErrorResponseSerializer(ApiErrorResponseSerializer):
    """Dashboard MCP endpoints use the standard management API error envelope."""

    retry_after = serializers.IntegerField(required=False)


class MCPConnectionResultSerializer(MCPConnectionSerializer):
    mcp_url = serializers.CharField(read_only=True, allow_null=True)

    class Meta(MCPConnectionSerializer.Meta):
        fields = [*MCPConnectionSerializer.Meta.fields, "mcp_url"]
        read_only_fields = [*MCPConnectionSerializer.Meta.read_only_fields, "mcp_url"]


class MCPConnectionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPConnectionResultSerializer()


class MCPToolGroupsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPToolGroupConfigSerializer()


class MCPAnalyticsSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPUsageSummarySerializer()


class MCPAnalyticsToolsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPUsageToolBreakdownSerializer(many=True)


class MCPAnalyticsTimelineResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPUsageTimelineSerializer(many=True)


class MCPHealthResultSerializer(serializers.Serializer):
    healthy = serializers.BooleanField()
    tool_count = serializers.IntegerField()
    version = serializers.CharField()


class MCPHealthResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPHealthResultSerializer()


class MCPSessionListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPSessionSerializer(many=True)


class MCPSessionRevokeResultSerializer(serializers.Serializer):
    message = serializers.CharField()


class MCPSessionRevokeResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPSessionRevokeResultSerializer()


class MCPToolCallRequestSerializer(serializers.Serializer):
    tool_name = serializers.CharField()
    params = serializers.DictField(required=False, default=dict)
    session_id = serializers.UUIDField(required=False, allow_null=True)


class MCPToolCallResultSerializer(serializers.Serializer):
    content = serializers.CharField(allow_blank=True, allow_null=True)
    data = serializers.JSONField(allow_null=True)
    is_error = serializers.BooleanField()
    error_code = serializers.CharField(allow_blank=True, allow_null=True)


class MCPToolCallResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPToolCallResultSerializer()
    session_id = serializers.UUIDField()


class MCPToolListResultSerializer(serializers.Serializer):
    tools = ToolDiscoveryItemSerializer(many=True)
    total = serializers.IntegerField()
    session_id = serializers.UUIDField(allow_null=True)


class MCPToolListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPToolListResultSerializer()


class MCPToolGroupChoiceSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    checked = serializers.BooleanField(required=False)
    enabled = serializers.BooleanField(required=False)


class MCPOAuthAuthorizeResponseResultSerializer(serializers.Serializer):
    client_name = serializers.CharField()
    client_id = serializers.CharField()
    redirect_uri = serializers.CharField()
    state = serializers.CharField(allow_blank=True)
    available_groups = MCPToolGroupChoiceSerializer(many=True)


class MCPOAuthAuthorizeResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPOAuthAuthorizeResponseResultSerializer()


class MCPOAuthConsentRequestSerializer(serializers.Serializer):
    client_id = serializers.CharField()
    redirect_uri = serializers.CharField()
    state = serializers.CharField(required=False, allow_blank=True)
    approved = serializers.BooleanField(required=False, default=False)
    selected_groups = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )


class MCPOAuthRedirectResultSerializer(serializers.Serializer):
    redirect_url = serializers.CharField()


class MCPOAuthRedirectResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPOAuthRedirectResultSerializer()


class MCPOAuthTokenRequestSerializer(serializers.Serializer):
    grant_type = serializers.ChoiceField(
        choices=("authorization_code", "refresh_token")
    )
    code = serializers.CharField(required=False)
    refresh_token = serializers.CharField(required=False)
    client_id = serializers.CharField()
    client_secret = serializers.CharField()
    redirect_uri = serializers.CharField(required=False)


class MCPOAuthTokenResponseSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    token_type = serializers.ChoiceField(choices=("Bearer",))
    expires_in = serializers.IntegerField()
    refresh_token = serializers.CharField(required=False)
    scope = serializers.CharField(allow_blank=True)


class MCPOAuthTokenErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()
    error_description = serializers.CharField(required=False)


class MCPOAuthApproveInfoResultSerializer(serializers.Serializer):
    client_name = serializers.CharField()
    client_id = serializers.CharField()
    scopes = serializers.ListField(child=serializers.CharField())
    redirect_uri = serializers.CharField()
    available_groups = MCPToolGroupChoiceSerializer(many=True)


class MCPOAuthApproveInfoResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = MCPOAuthApproveInfoResultSerializer()


class MCPOAuthApproveRequestSerializer(serializers.Serializer):
    request_id = serializers.CharField()
    approved = serializers.BooleanField(required=False, default=False)
    selected_groups = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

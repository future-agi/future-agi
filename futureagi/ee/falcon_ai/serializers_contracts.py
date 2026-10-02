from rest_framework import serializers

from ee.falcon_ai.serializers import (
    ConversationDetailSerializer,
    ConversationListSerializer,
)
from ee.falcon_ai.serializers_connectors import (
    MCPConnectorDetailSerializer,
    MCPConnectorListSerializer,
)
from ee.falcon_ai.serializers_memory import FalconMemorySerializer
from ee.falcon_ai.serializers_skills import SkillDetailSerializer, SkillListSerializer
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tracer.serializers.filters import StrictInputSerializer


class FalconErrorResponseSerializer(ApiTextErrorResponseSerializer):
    pass


class FalconEmptyRequestSerializer(StrictInputSerializer):
    pass


class ConversationCreateRequestSerializer(StrictInputSerializer):
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    context_page = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )
    # Hidden conversations (e.g. the Error Feed "Fix" tab's embedded RCA runs)
    # are excluded from Falcon's chat-history list — they're internal, not chats
    # the user started.
    hidden = serializers.BooleanField(required=False, default=False)


class ConversationUpdateRequestSerializer(StrictInputSerializer):
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ConversationListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    results = ConversationListSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class ConversationDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ConversationDetailSerializer()


class StreamStatusResultSerializer(serializers.Serializer):
    stream_status = serializers.CharField()


class StreamStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = StreamStatusResultSerializer()


class MessageFeedbackResultSerializer(serializers.Serializer):
    feedback = serializers.CharField(allow_blank=True)


class MessageFeedbackResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MessageFeedbackResultSerializer()


class FileUploadResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    size = serializers.IntegerField()
    content_type = serializers.CharField()
    url = serializers.URLField()


class FileUploadResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = FileUploadResultSerializer()


class MCPConnectorListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    results = MCPConnectorListSerializer(many=True)


class MCPConnectorDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPConnectorDetailSerializer()


class MCPConnectorUpdateRequestSerializer(StrictInputSerializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    server_url = serializers.URLField(required=False)
    transport = serializers.ChoiceField(
        choices=("sse", "streamable_http"), required=False
    )
    auth_type = serializers.ChoiceField(
        choices=("none", "api_key", "bearer", "oauth"), required=False
    )
    auth_header_name = serializers.CharField(
        required=False, allow_blank=True, max_length=100
    )
    auth_header_value = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


class MCPConnectorDiscoverResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPConnectorDetailSerializer()
    discovered_count = serializers.IntegerField()


class MCPConnectorTestResultSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    status_code = serializers.IntegerField(required=False)
    error = serializers.CharField(required=False, allow_blank=True)


class MCPConnectorTestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPConnectorTestResultSerializer(required=False)
    error = serializers.CharField(required=False, allow_blank=True)


class MCPConnectorAuthenticateResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPConnectorDetailSerializer(required=False)
    auth_type = serializers.CharField(required=False, allow_blank=True)
    authorization_url = serializers.URLField(required=False)
    message = serializers.CharField(required=False, allow_blank=True)


class MCPOAuthCallbackQuerySerializer(StrictInputSerializer):
    code = serializers.CharField(required=False, allow_blank=True)
    state = serializers.CharField(required=False, allow_blank=True)
    error = serializers.CharField(required=False, allow_blank=True)
    error_description = serializers.CharField(required=False, allow_blank=True)


class MCPOAuthCallbackHtmlResponseSerializer(serializers.Serializer):
    html = serializers.CharField(help_text="HTML page that posts to opener and closes.")


class FalconMemoryListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    results = FalconMemorySerializer(many=True)


class FalconMemoryDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = FalconMemorySerializer()


class SkillListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    results = SkillListSerializer(many=True)


class SkillDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = SkillDetailSerializer()


class SkillUpdateRequestSerializer(StrictInputSerializer):
    name = serializers.CharField(required=False, max_length=100)
    description = serializers.CharField(required=False, allow_blank=True)
    icon = serializers.CharField(required=False, max_length=50)
    instructions = serializers.CharField(required=False)
    tool_names = serializers.ListField(child=serializers.CharField(), required=False)
    trigger_phrases = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    is_active = serializers.BooleanField(required=False)


class QuickAnalysisResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = serializers.CharField()

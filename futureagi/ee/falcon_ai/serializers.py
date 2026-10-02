from rest_framework import serializers

from ee.falcon_ai.models import Conversation, Message
from tracer.serializers.filters import StrictInputSerializer


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        ref_name = "FalconMessage"
        fields = [
            "id",
            "conversation",
            "role",
            "content",
            "thoughts",
            "tool_calls",
            "completion_card",
            "files",
            "feedback",
            "input_tokens",
            "output_tokens",
            "model_used",
            "latency_ms",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ConversationListSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Conversation
        ref_name = "FalconConversationList"
        fields = [
            "id",
            "title",
            "context_page",
            "created_at",
            "updated_at",
            "message_count",
            "last_message_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ConversationDetailSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        ref_name = "FalconConversationDetail"
        fields = [
            "id",
            "user",
            "organization",
            "workspace",
            "title",
            "context_page",
            "metadata",
            "messages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
        ]


class MessageFeedbackSerializer(StrictInputSerializer):
    feedback = serializers.ChoiceField(
        choices=[
            ("thumbs_up", "Thumbs Up"),
            ("thumbs_down", "Thumbs Down"),
            ("", "Clear"),
        ]
    )

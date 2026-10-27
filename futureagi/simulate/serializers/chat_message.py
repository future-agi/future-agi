from rest_framework import serializers

from simulate.models.chat_message import ChatMessageModel


class ChatMessageSerializer(serializers.ModelSerializer):
    """Serializer for ChatMessageModel - stores chat messages for call executions"""

    start_time_ms = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessageModel
        fields = [
            "id",
            "role",
            "messages",
            "content",
            "session_id",
            "tool_calls",
            "created_at",
            "start_time_ms",
        ]
        read_only_fields = ["id", "created_at"]

    def get_start_time_ms(self, obj) -> int | None:
        """Offset from the call start, on the clock of the call's tool calls.

        Only harness transcripts record when each turn was delivered; null otherwise.
        ``created_at`` is not a turn time: rows are bulk-inserted after the run.
        """
        times = [
            item["start_time_ms"]
            for item in obj.content or []
            if isinstance(item, dict) and isinstance(item.get("start_time_ms"), int)
        ]
        return min(times) if times else None

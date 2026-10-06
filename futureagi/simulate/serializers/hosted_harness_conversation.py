from __future__ import annotations

from rest_framework import serializers


class HarnessConversationMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=20_000, trim_whitespace=False)
    client_request_id = serializers.RegexField(r"^[A-Za-z0-9_-]{1,128}$")
    kind = serializers.ChoiceField(
        choices=(
            "user_message",
            "user_response",
            "approval",
            "interrupt",
            "cancel_operation",
        ),
        required=False,
        default="user_message",
    )
    reply_to = serializers.UUIDField(required=False, allow_null=True)
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        kind = attrs["kind"]
        reply_to = attrs.get("reply_to")
        if kind in {"user_response", "approval"} and reply_to is None:
            raise serializers.ValidationError(
                {"reply_to": "This field is required when answering ALK."}
            )
        if kind == "user_message" and reply_to is not None:
            raise serializers.ValidationError(
                {"reply_to": "A new message cannot answer a blocking question."}
            )
        return attrs


class HarnessConversationAdjustmentSerializer(serializers.Serializer):
    instruction = serializers.CharField(
        min_length=1,
        max_length=2000,
        trim_whitespace=True,
    )
    client_request_id = serializers.CharField(
        max_length=128, required=False, allow_blank=False
    )
class HarnessConversationAdjustmentResponseSerializer(serializers.Serializer):
    adjustment_id = serializers.UUIDField()
    client_request_id = serializers.CharField(
        allow_null=True, required=False, allow_blank=False
    )
    instruction = serializers.CharField()
    target_stage = serializers.CharField()
    scenario_delta = serializers.IntegerField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()



class HarnessConversationMessageSerializer(serializers.Serializer):
    message_id = serializers.UUIDField()
    sequence = serializers.IntegerField(min_value=1)
    role = serializers.ChoiceField(choices=("user", "assistant", "system"))
    kind = serializers.ChoiceField(
        choices=("message", "question", "confirmation", "status")
    )
    state = serializers.ChoiceField(
        choices=("queued", "delivered", "streaming", "completed", "failed")
    )
    stage = serializers.CharField(allow_blank=True)
    content = serializers.CharField(allow_blank=True)
    payload = serializers.JSONField()
    invocation_id = serializers.CharField(allow_null=True, required=False)
    function_call_id = serializers.CharField(allow_null=True, required=False)
    reply_to = serializers.UUIDField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()


class HarnessConversationEventReadSerializer(serializers.Serializer):
    event_id = serializers.CharField()
    sequence = serializers.IntegerField(min_value=1)
    kind = serializers.CharField()
    message_id = serializers.UUIDField(allow_null=True, required=False)
    stage = serializers.CharField(allow_blank=True)
    invocation_id = serializers.CharField(allow_null=True, required=False)
    function_call_id = serializers.CharField(allow_null=True, required=False)
    payload = serializers.JSONField()
    emitted_at = serializers.DateTimeField()


class HarnessConversationRuntimeSerializer(serializers.Serializer):
    state = serializers.CharField()
    warm_until = serializers.DateTimeField(allow_null=True)
    degraded = serializers.BooleanField()
    available = serializers.BooleanField()


class HarnessConversationReadSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    state = serializers.CharField()
    stage = serializers.CharField()
    active_invocation_id = serializers.CharField(allow_null=True)
    blocking_input = serializers.JSONField(allow_null=True)
    messages = HarnessConversationMessageSerializer(many=True)
    events = HarnessConversationEventReadSerializer(many=True)
    event_watermark = serializers.IntegerField(min_value=0)
    runtime = HarnessConversationRuntimeSerializer()


class HarnessConversationCommandQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(min_value=0, required=False, default=0)


class HarnessConversationEventSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(
        choices=("futureagi.harness-conversation-event.v1",)
    )
    event_id = serializers.RegexField(r"^[A-Za-z0-9_-]{1,128}$")
    conversation_id = serializers.UUIDField()
    sequence = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(
        choices=(
            "turn_started",
            "assistant_delta",
            "assistant_message",
            "stage_changed",
            "authoring_activity",
            "tool_started",
            "tool_result",
            "question_requested",
            "confirmation_requested",
            "turn_interrupted",
            "turn_completed",
            "checkpoint_committed",
            "capability_changed",
        )
    )
    message_id = serializers.UUIDField(required=False, allow_null=True)
    stage = serializers.CharField(max_length=32, required=False, allow_blank=True)
    invocation_id = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=False
    )
    function_call_id = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=False
    )
    emitted_at = serializers.DateTimeField()
    payload = serializers.JSONField()
    digest = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")


class HarnessConversationEventBatchSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(
        choices=("futureagi.harness-conversation-event.v1",)
    )
    acknowledged_through = serializers.IntegerField(min_value=0)
    events = HarnessConversationEventSerializer(many=True, max_length=100)


class HarnessConversationEventAckSerializer(serializers.Serializer):
    acked_through_sequence = serializers.IntegerField(min_value=0)


class HarnessConversationTranscriptKeySerializer(serializers.Serializer):
    project_key = serializers.CharField(min_length=1, max_length=255)
    session_id = serializers.CharField(min_length=1, max_length=255)
    subpath = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=512,
    )


class HarnessConversationTranscriptQuerySerializer(
    HarnessConversationTranscriptKeySerializer
):
    pass


class HarnessConversationTranscriptAppendSerializer(
    HarnessConversationTranscriptKeySerializer
):
    entries = serializers.ListField(
        child=serializers.JSONField(),
        min_length=1,
        max_length=500,
    )

    def validate_entries(self, entries):
        if any(
            not isinstance(entry, dict) or not str(entry.get("type") or "")
            for entry in entries
        ):
            raise serializers.ValidationError(
                "Each transcript entry must be an object with a type."
            )
        return entries


class HarnessConversationTranscriptAppendResponseSerializer(serializers.Serializer):
    appended = serializers.IntegerField(min_value=0)


class HarnessConversationTranscriptReadSerializer(serializers.Serializer):
    entries = serializers.JSONField(allow_null=True)
    subkeys = serializers.ListField(child=serializers.CharField())


class HarnessConversationWorkspaceResponseSerializer(serializers.Serializer):
    digest = serializers.RegexField(r"^sha256:[0-9a-f]{64}$")
    size = serializers.IntegerField(min_value=0)


class HarnessConversationRunStatusSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    state = serializers.CharField()
    stage = serializers.CharField()
    completed_scenarios = serializers.IntegerField(min_value=0)
    failed_scenarios = serializers.IntegerField(min_value=0)
    total_scenarios = serializers.IntegerField(min_value=0)
    receipts = serializers.JSONField()


class HarnessConversationRerunSerializer(serializers.Serializer):
    pass

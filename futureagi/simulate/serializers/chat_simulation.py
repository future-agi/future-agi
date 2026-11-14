from rest_framework import serializers


class ChatToolCallFunctionSerializer(serializers.Serializer):
    name = serializers.CharField()
    arguments = serializers.CharField()


class ChatToolCallSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    function = ChatToolCallFunctionSerializer()


class ChatMessageContractSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=("user", "assistant", "tool"))
    content = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    tool_call_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )
    name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    metadata = serializers.DictField(required=False, allow_null=True)
    tool_calls = ChatToolCallSerializer(
        many=True, required=False, allow_null=True
    )


class SendChatRequestSerializer(serializers.Serializer):
    """DRF contract mirror of the SendChatRequest Pydantic schema."""

    messages = ChatMessageContractSerializer(
        many=True, required=False, allow_null=True
    )
    metrics = serializers.DictField(required=False, allow_null=True)
    initiate_chat = serializers.BooleanField(required=False, default=False)


class ChatSendMessageResultSerializer(serializers.Serializer):
    input_message = ChatMessageContractSerializer(
        many=True, required=False, allow_null=True
    )
    output_message = ChatMessageContractSerializer(
        many=True, required=False, allow_null=True
    )
    message_history = ChatMessageContractSerializer(many=True)
    chat_ended = serializers.BooleanField(required=False, default=False)


class ChatSendMessageResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ChatSendMessageResultSerializer()


class RunTestNameResultSerializer(serializers.Serializer):
    run_test_id = serializers.UUIDField()
    run_test_name = serializers.CharField()


class RunTestNameResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = RunTestNameResultSerializer()


class RunTestChatExecutionResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    execution_id = serializers.UUIDField()
    run_test_id = serializers.UUIDField()
    status = serializers.CharField()
    total_scenarios = serializers.ListField(child=serializers.UUIDField())


class RunTestChatExecutionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = RunTestChatExecutionResultSerializer()


class TestExecutionChatBatchResultSerializer(serializers.Serializer):
    call_execution_ids = serializers.ListField(child=serializers.UUIDField())
    has_more = serializers.BooleanField()
    batched_scenarios = serializers.ListField(child=serializers.UUIDField())


class TestExecutionChatBatchResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TestExecutionChatBatchResultSerializer()


class ChatSDKCodeResultSerializer(serializers.Serializer):
    installation_guide = serializers.CharField()
    sdk_code = serializers.CharField()
    run_test_id = serializers.UUIDField()
    run_test_name = serializers.CharField()


class ChatSDKCodeResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = ChatSDKCodeResultSerializer()

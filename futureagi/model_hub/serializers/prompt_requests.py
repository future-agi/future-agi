"""Request contracts for prompt-template operations."""

from rest_framework import serializers

from model_hub.serializers.experiments import (
    MessageItemSerializer,
    PromptConfigurationSerializer,
    PromptModelParamsSerializer,
)
from model_hub.serializers.prompt_template import (
    PromptHistoryExecutionSerializer,
    PromptTemplateSerializer,
)
from tfc.utils.serializer_fields import JsonValueField, StringOrObjectField


class PromptTemplatePatchSerializer(PromptTemplateSerializer):
    class Meta(PromptTemplateSerializer.Meta):
        extra_kwargs = {"name": {"required": False}}


class PromptRunModelConfigurationSerializer(
    PromptConfigurationSerializer, PromptModelParamsSerializer
):
    model = StringOrObjectField(required=False)

    def get_fields(self):
        fields = super().get_fields()
        # The workbench sends null for unset provider options.
        for name, field in fields.items():
            if name != "model":
                field.allow_null = True
        return fields


class PromptRunConfigurationSerializer(serializers.Serializer):
    messages = MessageItemSerializer(many=True, required=False)
    configuration = PromptRunModelConfigurationSerializer(required=False)


class PromptRunRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    version = serializers.CharField(required=False)
    prompt_config = PromptRunConfigurationSerializer(
        many=True, required=False, allow_empty=False
    )
    variable_names = serializers.DictField(child=JsonValueField(), required=False)
    placeholders = JsonValueField(required=False, allow_null=True)
    evaluation_configs = serializers.ListField(child=JsonValueField(), required=False)
    source = serializers.CharField(required=False)
    is_run = JsonValueField(
        required=False,
        allow_null=True,
        help_text='Use "prompt" to run the LLM, "eval" for evaluations, or false to save configuration.',
    )
    is_sdk = serializers.BooleanField(required=False)
    run_index = serializers.IntegerField(required=False, allow_null=True, min_value=0)

    def validate(self, data):
        if not data.get("version") and not data.get("prompt_config"):
            raise serializers.ValidationError(
                "Supply a version or a nonempty prompt_config."
            )
        return data


class PromptVersionsQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(required=False, min_value=1)
    # The versions list never capped `limit`; documenting it must not start
    # rejecting callers that already passed values above 100.
    limit = serializers.IntegerField(required=False, min_value=1)


class PromptRunStatusQuerySerializer(serializers.Serializer):
    template_version = serializers.CharField(required=False, allow_blank=True)


class PromptRunStatusResultSerializer(serializers.Serializer):
    status = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(allow_null=True, allow_blank=True)
    executions_result = PromptHistoryExecutionSerializer()


class PromptRunStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = PromptRunStatusResultSerializer()


class PromptTemplateListQuerySerializer(serializers.Serializer):
    modality = serializers.ListField(child=serializers.CharField(), required=False)

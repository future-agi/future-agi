from rest_framework import serializers

from model_hub.models.api_key import ApiKey
from tfc.utils.serializer_fields import JsonValueField


class ApiKeyCreateSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    key = serializers.CharField(max_length=2500, allow_blank=True, allow_null=True)


class ApiKeyRequestSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    key = serializers.CharField(
        max_length=2500,
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    config_json = serializers.JSONField(required=False, allow_null=True)


class ApiKeyResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    provider = serializers.CharField(max_length=50)
    organization = serializers.UUIDField(required=False, allow_null=True)
    masked_actual_key = serializers.JSONField(required=False, allow_null=True)


class ApiKeyListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    previous = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    results = ApiKeyResponseSerializer(many=True)


class ApiKeySuccessResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ApiKeyResponseSerializer()


class ApiKeySerializer(serializers.ModelSerializer):
    key = serializers.CharField(
        max_length=2500,
        required=False,
        allow_blank=True,
        allow_null=True,
        write_only=True,
    )
    masked_actual_key = serializers.SerializerMethodField()
    config_json = serializers.JSONField(
        required=False,
        allow_null=True,
        write_only=True,
    )

    def get_masked_actual_key(self, obj):
        return obj.masked_actual_key

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "provider",
            "key",
            "organization",
            "masked_actual_key",
            "config_json",
        ]
        read_only_fields = ["organization"]


class LitellmSerializer(serializers.Serializer):
    # Required fields
    dataset_id = serializers.CharField(max_length=255)
    model = serializers.CharField(max_length=255)
    name = serializers.CharField(max_length=255)
    concurrency = serializers.IntegerField(default=5)
    messages = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        help_text="List of messages with format [{'role': 'user/assistant', 'content': 'text'}]",
    )

    # Optional fields with validation
    output_format = serializers.ChoiceField(
        choices=["array", "string", "number", "object", "audio", "image"],
        default="string",
        help_text="Output format type. Defaults to 'string'.",
    )
    temperature = serializers.FloatField(
        min_value=0.0,
        max_value=1.0,
        required=False,
        allow_null=True,
        default=None,
        help_text="Controls the randomness. Value between 0 and 1.",
    )
    frequency_penalty = serializers.FloatField(
        min_value=-2.0,
        max_value=2.0,
        required=False,
        allow_null=True,
        default=None,
        help_text="Penalty for word repetition. Value between -2 and 2.",
    )
    presence_penalty = serializers.FloatField(
        min_value=-2.0,
        max_value=2.0,
        required=False,
        allow_null=True,
        default=None,
        help_text="Penalty for new word usage. Value between -2 and 2.",
    )
    max_tokens = serializers.IntegerField(
        min_value=1,
        max_value=65536,
        required=False,
        allow_null=True,
        default=None,
        help_text="Maximum number of tokens to generate. Null = use provider default.",
    )
    top_p = serializers.FloatField(
        min_value=0.0,
        max_value=1.0,
        required=False,
        allow_null=True,
        default=None,
        help_text="Controls diversity via nucleus sampling. Value between 0 and 1.",
    )
    response_format = serializers.JSONField(
        required=False,
        help_text="JSON schema for response format if required. Defaults to None.",
    )
    tool_choice = serializers.ChoiceField(
        choices=["auto", "required", None],
        default=None,
        required=False,
        help_text="Tool selection mode: 'auto' or 'required'.",
    )
    tools = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text="List of tools with tool properties if available.",
    )

    # Custom Validation for Messages Structure
    def validate_messages(self, value: list[dict[str, str]]) -> list[dict[str, str]]:
        for message in value:
            if "role" not in message or message["role"] not in {
                "user",
                "assistant",
                "system",
            }:
                raise serializers.ValidationError(
                    "Each message must have a 'role' key with value 'user', 'assistant', or 'system'."
                )
            if "content" not in message or not isinstance(message["content"], str):
                raise serializers.ValidationError(
                    "Each message must have a 'content' key with a string value."
                )
        return value


class PromptConfigSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=255, required=False, allow_blank=True)
    # Free-form config: values are mixed JSON (model_name str, isAvailable
    # bool, booleans/dropdowns objects, …). Use JsonValueField so the contract
    # doesn't constrain every value to a string. allow_null because the FE
    # sends null for unset model params (temperature, top_p, …) meaning "use
    # provider default" — JSONField's default allow_null=False would 400 them.
    run_prompt_config = serializers.DictField(
        child=JsonValueField(allow_null=True), required=False
    )
    messages = serializers.ListField(
        # content can be a string or a list of typed parts — keep values open.
        child=serializers.DictField(child=JsonValueField()),
        required=False,
        help_text="List of messages with format [{'role': 'user/assistant', 'content': 'text'}]",
    )
    temperature = serializers.FloatField(
        min_value=0.0,
        max_value=2.0,
        required=False,
        allow_null=True,
        help_text="Controls the randomness. Value between 0 and 2.",
    )
    frequency_penalty = serializers.FloatField(
        min_value=-2.0,
        max_value=2.0,
        required=False,
        allow_null=True,
        help_text="Penalty for word repetition. Value between -2 and 2.",
    )
    presence_penalty = serializers.FloatField(
        min_value=-2.0,
        max_value=2.0,
        required=False,
        allow_null=True,
        help_text="Penalty for new word usage. Value between -2 and 2.",
    )
    max_tokens = serializers.IntegerField(
        min_value=1,
        max_value=65536,
        required=False,
        allow_null=True,
        help_text="Maximum number of tokens to generate. Null = use provider default.",
    )
    top_p = serializers.FloatField(
        min_value=0.0,
        max_value=1.0,
        required=False,
        allow_null=True,
        help_text="Controls diversity via nucleus sampling. Value between 0 and 1.",
    )
    response_format = JsonValueField(
        required=False,
        allow_null=True,
        help_text="JSON schema for response format if required. Can be a JSON object or string. Defaults to None.",
    )
    tool_choice = serializers.ChoiceField(
        choices=["auto", "required", None],
        required=False,
        allow_null=True,
        help_text="Tool selection mode: 'auto' or 'required'.",
    )
    tools = serializers.ListField(
        child=serializers.DictField(child=JsonValueField()),
        required=False,
        allow_null=True,
        help_text="List of tools with tool properties if available.",
    )
    output_format = serializers.ChoiceField(
        choices=["array", "string", "number", "object", "audio", "image"],
        required=False,
        allow_null=True,
        help_text="Output format type.",
    )
    concurrency = serializers.IntegerField(
        min_value=1,
        max_value=10,
        required=False,
        allow_null=True,
        help_text="Number of concurrent operations allowed. Maximum 10.",
    )

    # def validate_response_format(self, value):
    #     if value is None:
    #         return value

    #     required_fields = ['name', 'strict', 'schema']
    #     for field in required_fields:
    #         if field not in value:
    #             raise serializers.ValidationError(f"Missing required field: {field}")

    #     schema = value.get('schema', {})
    #     if not isinstance(schema, dict):
    #         raise serializers.ValidationError("Schema must be a dictionary")

    #     if 'type' not in schema:
    #         raise serializers.ValidationError("Schema must specify a 'type'")

    #     if 'properties' not in schema:
    #         raise serializers.ValidationError("Schema must specify 'properties'")

    #     if not isinstance(schema['properties'], dict):
    #         raise serializers.ValidationError("Schema properties must be a dictionary")

    #     for prop_name, prop_value in schema['properties'].items():
    #         if not isinstance(prop_value, dict):
    #             raise serializers.ValidationError(f"Property '{prop_name}' must be a dictionary")
    #         if 'type' not in prop_value:
    #             raise serializers.ValidationError(f"Property '{prop_name}' must specify a 'type'")

    #     return value

    def validate_messages(self, value):
        if not value:
            raise serializers.ValidationError("Messages list cannot be empty")

        # Validate first message
        first_message = value[0]
        if "role" in first_message and first_message["role"] == "assistant":
            raise serializers.ValidationError(
                "First message cannot be from 'assistant'. It must be from 'system' or 'user'."
            )

        for message in value:
            if "role" not in message or message["role"] not in {
                "user",
                "assistant",
                "system",
            }:
                raise serializers.ValidationError(
                    "Each message must have a 'role' key with value 'user', 'assistant', or 'system'."
                )
            if "content" not in message or (
                not isinstance(message["content"], str)
                and not isinstance(message["content"], list)
            ):
                raise serializers.ValidationError(
                    "Each message must have a 'content' key with a string or list value."
                )

            # User messages must have non-empty content
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str) and not content.strip():
                    raise serializers.ValidationError(
                        "User messages must have non-empty content."
                    )
                elif isinstance(content, list) and not any(
                    self._has_valid_content(item) for item in content
                ):
                    raise serializers.ValidationError(
                        "User messages must have at least one non-empty content item."
                    )
        return value

    def _has_valid_content(self, item):
        """Check if a content item has a non-empty value."""
        if not isinstance(item, dict):
            return False
        item_type = item.get("type", "")
        if item_type == "text":
            return bool(item.get("text", "").strip())
        elif item_type == "image_url":
            outer = item.get("image_url") or item.get("imageUrl") or {}
            return bool(outer.get("url", "").strip())
        elif item_type == "audio_url":
            outer = item.get("audio_url") or item.get("audioUrl") or {}
            return bool(outer.get("url", "").strip())
        elif item_type == "pdf_url":
            outer = item.get("pdf_url") or item.get("pdfUrl") or {}
            return bool(outer.get("url", "").strip())
        return False

    def validate(self, attrs):
        run_prompt_config = attrs.get("run_prompt_config", {})

        # Validate model name is non-empty when provided
        if run_prompt_config and "modelName" in run_prompt_config:
            model_name = run_prompt_config.get("modelName")
            if model_name is not None and not str(model_name).strip():
                raise serializers.ValidationError(
                    {"run_prompt_config": "Model name is required."}
                )

        # Validate voice is required for TTS model type
        model_type = run_prompt_config.get("modelType", "")
        if model_type == "tts":
            voice = run_prompt_config.get("voice", "")
            if not voice or not str(voice).strip():
                raise serializers.ValidationError(
                    {"run_prompt_config": "Voice is required for TTS models."}
                )

        return attrs


class AddRunPromptSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField(required=True)
    name = serializers.CharField(required=True, min_length=1)
    config = PromptConfigSerializer(required=False, default=dict)


class PreviewRunPromptSerializer(AddRunPromptSerializer):
    first_n_rows = serializers.IntegerField(min_value=1, required=False)
    row_indices = serializers.ListField(
        child=serializers.IntegerField(min_value=0),
        required=False,
        allow_empty=False,
        help_text="List of row indices to preview. Must contain at least one integer.",
    )

    def validate(self, attrs):
        if (not attrs.get("first_n_rows") and not attrs.get("row_indices")) or (
            attrs.get("first_n_rows") and attrs.get("row_indices")
        ):
            raise serializers.ValidationError(
                "Either of 'first_n_rows' or 'row_indices' must be provided."
            )
        return attrs


class EditRunPromptColumnSerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField(required=True)
    column_id = serializers.UUIDField(required=True)
    name = serializers.CharField(required=False, allow_null=True)
    config = PromptConfigSerializer(required=False, allow_null=True)

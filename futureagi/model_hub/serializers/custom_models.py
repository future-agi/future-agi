from rest_framework import serializers

from model_hub.models.api_key import mask_key
from model_hub.models.custom_models import CustomAIModel


class CustomAIModelSerializer(serializers.ModelSerializer):
    config_json = serializers.SerializerMethodField()
    user = serializers.SerializerMethodField()

    class Meta:
        model = CustomAIModel
        fields = [
            "id",
            "created_at",
            "user_model_id",
            "deleted",
            "provider",
            "input_token_cost",
            "output_token_cost",
            "config_json",
            "user",
            "updated_at",
        ]

    def get_config_json(self, obj):
        if obj.key_config:
            if obj.provider == "custom":
                # Custom header names and URL query parameters are arbitrary;
                # prefix/suffix masking can reveal a short credential in full.
                return self._redact_custom_config(obj.actual_json)
            return mask_key(obj.actual_json)
        else:
            return None

    @classmethod
    def _redact_custom_config(cls, value):
        if isinstance(value, dict):
            return {key: cls._redact_custom_config(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_custom_config(item) for item in value]
        if isinstance(value, bool) or value is None:
            return value
        return "********"

    def get_user(self, obj):
        if obj.user:
            return obj.user.name
        return None


class CustomAIModelsSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomAIModel
        fields = (
            "id",
            # "model_type",
            "user_model_id",
            "baseline_model_environment",
            "baseline_model_version",
        )


class CustomAIModelsListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomAIModel
        fields = (
            "id",
            # "model_type",
            "user_model_id",
        )

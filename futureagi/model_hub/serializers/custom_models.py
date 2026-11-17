from rest_framework import serializers

from model_hub.models.custom_models import CustomAIModel
from model_hub.services.key_at_rest import UNREADABLE_MASK, KeyUnreadable


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
                try:
                    return self._redact_custom_config(obj.actual_json)
                except KeyUnreadable:
                    # A stored value this deployment cannot decrypt keeps its
                    # notice to enter the key again; every other value is hidden.
                    return self._redact_custom_config(
                        obj.masked_json, keep=UNREADABLE_MASK
                    )
            return obj.masked_json
        else:
            return None

    @classmethod
    def _redact_custom_config(cls, value, keep=None):
        if isinstance(value, dict):
            return {
                key: cls._redact_custom_config(item, keep)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact_custom_config(item, keep) for item in value]
        if isinstance(value, bool) or value is None:
            return value
        if keep is not None and value == keep:
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

from rest_framework import serializers

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
            return obj.masked_json
        else:
            return None

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

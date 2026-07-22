from rest_framework import serializers

from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.choices import ProviderLogoUrls
from model_hub.utils.workspace_scope import request_organization, request_workspace
from simulate.models import SimulatorAgent


class SimulatorAgentSerializer(serializers.ModelSerializer):
    """Serializer for SimulatorAgent model."""

    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = SimulatorAgent
        fields = [
            "id",
            "name",
            "prompt",
            "voice_provider",
            "voice_name",
            "interrupt_sensitivity",
            "conversation_speed",
            "finished_speaking_sensitivity",
            "model",
            "llm_temperature",
            "max_call_duration_in_minutes",
            "initial_message_delay",
            "initial_message",
            "created_at",
            "updated_at",
            "organization",
            "deleted",
            "deleted_at",
            "logo_url",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "organization",
            "deleted",
            "deleted_at",
            "logo_url",
        ]

    def get_logo_url(self, obj):
        """Get the logo URL for the model provider"""
        try:
            if obj.model:
                # Get the provider from the model name
                model_manager = LiteLLMModelManager(obj.model)
                provider = model_manager.get_provider(obj.model)
                return ProviderLogoUrls.get_url_by_provider(provider)
        except (ValueError, Exception):
            pass
        return None

    def create(self, validated_data):
        """Create a new SimulatorAgent instance"""
        request = self.context.get("request")
        if request:
            organization = request_organization(request)
            if organization is not None:
                validated_data["organization"] = organization

            workspace = request_workspace(request)
            if workspace is not None:
                validated_data["workspace"] = workspace

        return SimulatorAgent.objects.create(**validated_data)


class SimulatorAgentListResponseSerializer(serializers.Serializer):
    """Paginated response envelope for GET /simulate/simulator-agents/."""

    count = serializers.IntegerField(read_only=True)
    next = serializers.CharField(read_only=True, allow_null=True)
    previous = serializers.CharField(read_only=True, allow_null=True)
    results = SimulatorAgentSerializer(many=True, read_only=True)
    total_pages = serializers.IntegerField(read_only=True)
    current_page = serializers.IntegerField(read_only=True)


class SimulatorAgentDeleteResponseSerializer(serializers.Serializer):
    """Response shape for deleting a simulator agent."""

    message = serializers.CharField(read_only=True)


class SimulatorAgentValidationErrorResponseSerializer(serializers.Serializer):
    """Field-error map returned directly from DRF serializer validation."""

    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {"type": "string"},
            },
        }

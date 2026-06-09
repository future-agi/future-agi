from rest_framework import serializers

from agentcc.models.shadow_experiment import AgentccShadowExperiment
from agentcc.models.shadow_result import AgentccShadowResult


class AgentccShadowExperimentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentccShadowExperiment
        fields = [
            "id",
            "name",
            "description",
            "source_model",
            "shadow_model",
            "shadow_provider",
            "sample_rate",
            "status",
            "total_comparisons",
            "config",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "total_comparisons",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def validate_sample_rate(self, value):
        if value < 0.0 or value > 1.0:
            raise serializers.ValidationError("sample_rate must be between 0.0 and 1.0")
        return value


class AgentccShadowResultSerializer(serializers.ModelSerializer):
    """A single captured shadow comparison: the production (source) model's
    response side by side with a shadow model's response for the same request,
    plus latency, token, and status-code metrics. Read-only; produced by shadow
    experiments. Listed/read via list_agentcc_shadow_results /
    get_agentcc_shadow_result."""

    class Meta:
        model = AgentccShadowResult
        fields = [
            "id",
            "experiment",
            "request_id",
            "source_model",
            "shadow_model",
            "source_response",
            "shadow_response",
            "source_latency_ms",
            "shadow_latency_ms",
            "source_tokens",
            "shadow_tokens",
            "source_status_code",
            "shadow_status_code",
            "shadow_error",
            "prompt_hash",
            "created_at",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "experiment": {
                "help_text": "UUID of the shadow experiment that produced this result (from list_agentcc_shadow_experiments)."
            },
            "request_id": {
                "help_text": "Gateway request id this comparison was captured from."
            },
            "source_model": {"help_text": "The production (source) model name."},
            "shadow_model": {"help_text": "The shadow model name compared against."},
            "source_response": {"help_text": "Text response from the source model."},
            "shadow_response": {"help_text": "Text response from the shadow model."},
            "source_latency_ms": {
                "help_text": "Source model latency in milliseconds."
            },
            "shadow_latency_ms": {
                "help_text": "Shadow model latency in milliseconds."
            },
            "source_tokens": {"help_text": "Token count for the source response."},
            "shadow_tokens": {"help_text": "Token count for the shadow response."},
            "source_status_code": {"help_text": "HTTP status code of the source call."},
            "shadow_status_code": {"help_text": "HTTP status code of the shadow call."},
            "shadow_error": {
                "help_text": "Error message if the shadow call failed, else empty."
            },
            "prompt_hash": {
                "help_text": "Hash of the prompt used to group identical requests."
            },
        }

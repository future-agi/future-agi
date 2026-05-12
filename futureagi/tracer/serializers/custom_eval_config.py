from rest_framework import serializers

from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


class CustomEvalConfigSerializer(serializers.ModelSerializer):
    eval_template = serializers.PrimaryKeyRelatedField(
        queryset=EvalTemplate.objects.all(), many=False
    )
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False
    )
    kb_id = serializers.PrimaryKeyRelatedField(
        queryset=KnowledgeBaseFile.objects.all(),
        many=False,
        required=False,
        allow_null=True,
    )

    eval_group = serializers.SerializerMethodField()

    class Meta:
        model = CustomEvalConfig
        fields = [
            "id",
            "eval_template",
            "name",
            "config",
            "mapping",
            "project",
            "filters",
            "error_localizer",
            "kb_id",
            "model",
            "eval_group",
        ]

    def get_eval_group(self, obj):
        if obj.eval_group:
            return obj.eval_group.name
        return None

    def validate(self, attrs):
        eval_template = attrs.get("eval_template") or getattr(
            self.instance, "eval_template", None
        )
        if eval_template:
            attrs["config"] = normalize_eval_runtime_config(
                eval_template.config,
                (
                    attrs.get("config")
                    if "config" in attrs
                    else getattr(self.instance, "config", {})
                ),
            )
            # TH-4909: reject silent configs for agent-type templates. The
            # bulk-attach path used to persist `config={}` for AgentEvaluators
            # — the resulting CECs ran but never produced eval_logger rows.
            # After normalize, the template's fields should have been merged
            # in; if they still aren't present, both the CEC and the linked
            # template are incomplete and the eval can't dispatch.
            if getattr(eval_template, "eval_type", None) == "agent":
                merged = attrs["config"] or {}
                missing = [
                    field
                    for field in ("output", "rule_prompt")
                    if not merged.get(field)
                ]
                if missing:
                    raise serializers.ValidationError(
                        {
                            "config": (
                                f"Agent eval template '{eval_template.name}' "
                                f"is missing required field(s) {missing} in both "
                                f"the request and the linked template config. "
                                f"Cannot attach an incomplete agent eval."
                            )
                        }
                    )
        return attrs


class RunEvaluationSerializer(serializers.Serializer):
    custom_eval_config_id = serializers.UUIDField(required=True)
    project_version_id = serializers.UUIDField(required=True)


class GetCustomEvalTemplateSerializer(serializers.Serializer):
    eval_template_name = serializers.CharField(required=True)

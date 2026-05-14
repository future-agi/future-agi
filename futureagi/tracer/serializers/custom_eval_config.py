from rest_framework import serializers

from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


def is_rule_prompt_customized(custom_eval_config):
    """Return True when CustomEvalConfig.config.rule_prompt overrides the eval template default.

    Empty / whitespace-only overrides are treated as not-customized so the runtime falls back to
    the template. Comparison against both `eval_template.config.rule_prompt` and
    `eval_template.criteria` (the deterministic-eval slot) so saved-without-edit attachments
    don't trip the flag.
    """
    config = getattr(custom_eval_config, "config", None) or {}
    saved = (config.get("rule_prompt") or "").strip()
    if not saved:
        return False
    tpl = custom_eval_config.eval_template
    tpl_prompt = ((tpl.config or {}).get("rule_prompt") or "").strip()
    tpl_criteria = (tpl.criteria or "").strip()
    return saved != tpl_prompt and saved != tpl_criteria


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
    is_customized = serializers.SerializerMethodField()

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
            "is_customized",
        ]

    def get_eval_group(self, obj):
        if obj.eval_group:
            return obj.eval_group.name
        return None

    def get_is_customized(self, obj):
        return is_rule_prompt_customized(obj)

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
        return attrs


class RunEvaluationSerializer(serializers.Serializer):
    custom_eval_config_id = serializers.UUIDField(required=True)
    project_version_id = serializers.UUIDField(required=True)


class GetCustomEvalTemplateSerializer(serializers.Serializer):
    eval_template_name = serializers.CharField(required=True)

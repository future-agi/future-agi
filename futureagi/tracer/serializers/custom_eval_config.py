from rest_framework import serializers

from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.serializers.filters import StrictInputSerializer


class CustomEvalConfigSerializer(serializers.ModelSerializer):
    """A custom evaluation attached to a trace project — it runs an eval
    template against that project's spans/traces (the object the Observe →
    Evaluations UI creates). Pairs an ``eval_template`` with a ``project``, a
    ``mapping`` from the template's input variables to span attribute paths,
    and optional ``filters`` scoping which spans are evaluated. Listed/read via
    ``list_custom_eval_configs`` / ``get_custom_eval_config``; edit the mapping
    via ``update_custom_eval_config`` (PATCH).
    """

    eval_template = serializers.PrimaryKeyRelatedField(
        queryset=EvalTemplate.objects.all(),
        many=False,
        help_text=(
            "UUID of the eval template to run against the project's spans "
            "(from list_eval_templates / get_eval_template)."
        ),
    )
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        many=False,
        help_text=(
            "UUID of the trace project this eval runs on (from list_projects)."
        ),
    )
    kb_id = serializers.PrimaryKeyRelatedField(
        queryset=KnowledgeBaseFile.objects.all(),
        many=False,
        required=False,
        allow_null=True,
        help_text=(
            "Optional knowledge-base file UUID to ground retrieval-based evals."
        ),
    )

    eval_group = serializers.SerializerMethodField()

    # Explicit field so the help_text flows into the generated MCP tool schema
    # (auto-derived JSONFields render as a bare "Mapping" with an empty object,
    # which left Falcon unable to do eval variable mapping — TH-5442).
    mapping = serializers.JSONField(
        required=False,
        help_text=(
            "Variable mapping for the eval. JSON object whose KEYS are the eval "
            "template's input variables (call get_eval_template with this "
            "config's eval_template id to read its required_keys / optional_keys; "
            "find the template id via list_eval_templates search if you only "
            "have the name) and whose VALUES are span attribute paths that exist "
            "in this project (call get_span_eval_attributes with "
            'filters={"project_id": "<id>"} for the available paths). Cover '
            "EVERY required_key. "
            'Example: {"input": "llm.input", "output": "llm.output"}.'
        ),
    )

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
        extra_kwargs = {
            "name": {
                "help_text": "Human-readable name for this eval configuration."
            },
            "config": {
                "help_text": (
                    "Eval runtime config (e.g. thresholds, choice scores); its "
                    "shape depends on the eval template — usually omit to use "
                    "the template defaults."
                )
            },
            "filters": {
                "help_text": (
                    "Optional filter list (same shape the Observe UI sends) "
                    "scoping which spans are evaluated; omit to evaluate all "
                    "matching spans."
                )
            },
            "error_localizer": {
                "help_text": (
                    "When true, run error-localization analysis on failing "
                    "spans to pinpoint the cell/attribute responsible."
                )
            },
            "model": {
                "help_text": (
                    "Optional model override (provider/model id) used to run "
                    "the eval; omit to use the template's default model."
                )
            },
        }

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
        return attrs


class RunEvaluationSerializer(serializers.Serializer):
    custom_eval_config_id = serializers.UUIDField(required=True)
    project_version_id = serializers.UUIDField(required=True)


class GetCustomEvalTemplateSerializer(serializers.Serializer):
    eval_template_name = serializers.CharField(required=True)


class CustomEvalConfigListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    task_id = serializers.UUIDField(required=False)

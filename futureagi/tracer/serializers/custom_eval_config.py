from rest_framework import serializers

from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.serializers.filters import StrictInputSerializer


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
    # Use all_objects so soft-deleted versions are fetchable here; the
    # validate() method then explicitly rejects them with a clear error.
    # Without this, the default manager excludes deleted rows and the
    # "Cannot pin a deleted version" branch never fires.
    pinned_version = serializers.PrimaryKeyRelatedField(
        queryset=EvalTemplateVersion.all_objects.all(),
        required=False,
        allow_null=True,
        help_text="Pin to a specific template version for runtime.",
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
            "pinned_version",
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

        # Validate pinned_version belongs to this config's eval_template
        pinned = attrs.get("pinned_version")
        if pinned is not None and eval_template is not None:
            if pinned.eval_template_id != eval_template.id:
                raise serializers.ValidationError(
                    {"pinned_version": "Version does not belong to this eval template."}
                )
            if pinned.deleted:
                raise serializers.ValidationError(
                    {"pinned_version": "Cannot pin a deleted version."}
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

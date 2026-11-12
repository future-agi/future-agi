from django.db.models import Q
from rest_framework import serializers

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.eval_mapping import non_path_mapping_keys
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tfc.utils.tenant_scope import (
    caller_organization,
    scope_to_caller,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.serializers.filters import StrictInputSerializer


def caller_eval_templates(request=None):
    """System templates plus USER templates in a live accessible workspace.

    Legacy workspace-less USER rows follow the shared default-workspace rule.
    """
    if caller_organization(request) is None:
        return EvalTemplate.no_workspace_objects.none()
    user_templates = scope_to_caller(
        EvalTemplate.no_workspace_objects.filter(owner=OwnerChoices.USER.value),
        request,
    )
    return EvalTemplate.no_workspace_objects.filter(
        Q(owner=OwnerChoices.SYSTEM.value) | Q(pk__in=user_templates.values("pk")),
        deleted=False,
    )


class CustomEvalConfigSerializer(serializers.ModelSerializer):
    # The querysets below only name the models: __init__ replaces them with
    # the caller's own rows on every instance.
    eval_template = serializers.PrimaryKeyRelatedField(
        queryset=EvalTemplate.no_workspace_objects.none(), many=False
    )
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.no_workspace_objects.none(), many=False
    )
    kb_id = serializers.PrimaryKeyRelatedField(
        queryset=KnowledgeBaseFile.no_workspace_objects.none(),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from tracer.serializers.relation_scope import (
            eval_template_queryset,
            scope_relations,
        )

        scope_relations(
            self,
            {
                "project": ("organization", "workspace"),
                "kb_id": ("organization", "workspace"),
            },
        )
        self.fields["eval_template"].queryset = eval_template_queryset(
            self.context.get("request")
        )

    def get_eval_group(self, obj):
        if obj.eval_group:
            return obj.eval_group.name
        return None

    def validate_mapping(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("Mapping must be an object.")
        bad = non_path_mapping_keys(value)
        if bad:
            raise serializers.ValidationError(
                "Mapping values must be attribute path strings. "
                f"Non-string values for: {', '.join(bad)}."
            )
        return value

    def validate(self, attrs):
        eval_template = attrs.get("eval_template") or getattr(
            self.instance, "eval_template", None
        )
        if eval_template:
            config_sent = "config" in attrs
            # A template switch re-checks the kept config against the new one.
            template_changed = "eval_template" in attrs and str(
                getattr(self.instance, "eval_template_id", None)
            ) != str(eval_template.pk)
            attrs["config"] = normalize_eval_runtime_config(
                eval_template.config,
                (
                    attrs.get("config")
                    if "config" in attrs
                    else getattr(self.instance, "config", {})
                ),
            )
            if config_sent or template_changed:
                from model_hub.utils.scoring import validate_choice_score_override

                try:
                    validate_choice_score_override(eval_template, attrs["config"])
                except ValueError as e:
                    raise serializers.ValidationError({"config": [str(e)]}) from e
        return attrs


class RunEvaluationSerializer(serializers.Serializer):
    custom_eval_config_id = serializers.UUIDField(required=True)
    project_version_id = serializers.UUIDField(required=True)


class GetCustomEvalTemplateSerializer(serializers.Serializer):
    eval_template_name = serializers.CharField(required=True)


class CustomEvalConfigListQuerySerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False)
    task_id = serializers.UUIDField(required=False)

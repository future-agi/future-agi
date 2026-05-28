from rest_framework import serializers

from model_hub.models.eval_groups import EvalGroup
from model_hub.schema.eval_group import PageType
from tracer.serializers.filters import StrictInputSerializer, filter_list_field


class EvalGroupSerializer(serializers.ModelSerializer):
    """An eval group bundles multiple eval templates so they can be applied
    together to a dataset, prompt template, or experiment.

    Use eval groups to reuse an established quality bar (e.g. an "RAG quality"
    group containing groundedness, retrieval relevance, and answer relevance
    templates) instead of attaching evals one-by-one. Names must be unique per
    workspace. Use the dedicated apply-eval-group endpoint to attach a group
    to a target dataset/template.
    """

    id = serializers.UUIDField(
        read_only=True,
        help_text=(
            "Unique eval group identifier (UUID v4). **How to get it:** call "
            "`list_eval_groups` first to discover group IDs."
        ),
    )
    name = serializers.CharField(
        max_length=255,
        help_text=(
            "Human-readable name. Must be unique within the workspace. "
            "Examples: 'rag-quality-v2', 'safety-checks', 'tone-and-clarity'."
        ),
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text=(
            "Optional free-form description of when to use this group. "
            "Example: 'Standard set for retrieval-augmented QA experiments.'"
        ),
    )
    is_sample = serializers.BooleanField(
        required=False,
        help_text=(
            "Whether this group is a built-in sample (read-only for end users) "
            "rather than a workspace-created group. Defaults to false."
        ),
    )
    created_by = serializers.SerializerMethodField(
        help_text="Display name of the user who created the group."
    )

    class Meta:
        model = EvalGroup
        fields = [
            "id",
            "name",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
            "description",
            "created_by",
            "is_sample",
        ]
        read_only_fields = ["organization", "workspace"]

    def get_created_by(self, obj):
        """
        Return the name of the user who created this template.
        Returns None if created_by is None.
        """
        if obj.created_by:
            return obj.created_by.name
        return obj.organization.name if obj.organization else "Future-agi Built"


class ApplyEvalGroupFiltersSerializer(StrictInputSerializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    prompt_template_id = serializers.UUIDField(required=False, allow_null=True)
    dataset_id = serializers.UUIDField(required=False, allow_null=True)
    simulate_id = serializers.UUIDField(required=False, allow_null=True)
    experiment_id = serializers.UUIDField(required=False, allow_null=True)
    kb_id = serializers.UUIDField(required=False, allow_null=True)
    model = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    error_localizer = serializers.BooleanField(required=False, default=False)
    filters = filter_list_field(required=False, default=list)


class ApplyEvalGroupRequestSerializer(StrictInputSerializer):
    eval_group_id = serializers.UUIDField()
    filters = ApplyEvalGroupFiltersSerializer(
        required=False,
        default=dict,
    )
    page_id = serializers.ChoiceField(choices=[page.value for page in PageType])
    mapping = serializers.DictField(child=serializers.JSONField())
    deselected_evals = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    params = serializers.DictField(
        child=serializers.JSONField(),
        required=False,
        default=dict,
    )

    REQUIRED_FILTER_BY_PAGE = {
        PageType.EVAL_TASK.value: "project_id",
        PageType.PROMPT.value: "prompt_template_id",
        PageType.DATASET.value: "dataset_id",
        PageType.SIMULATE.value: "simulate_id",
        PageType.EXPERIMENT.value: "experiment_id",
    }

    def validate(self, data):
        data = super().validate(data)
        page_id = data.get("page_id")
        filters = data.get("filters") or {}
        required_key = self.REQUIRED_FILTER_BY_PAGE.get(page_id)
        if required_key and not filters.get(required_key):
            raise serializers.ValidationError(
                {"filters": {required_key: "This field is required for page_id."}}
            )
        return data

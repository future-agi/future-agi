from rest_framework import serializers

from model_hub.models.eval_groups import EvalGroup
from model_hub.schema.eval_group import PageType
from tracer.serializers.filters import StrictInputSerializer, filter_list_field


class EvalGroupSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()

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

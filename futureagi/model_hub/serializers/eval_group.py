from rest_framework import serializers

from model_hub.models.eval_groups import EvalGroup
from model_hub.schema.eval_group import PageType
from tfc.utils.serializer_fields import JsonValueField
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


# Response contracts of EvalGroupView.list and .retrieve, which build their
# rows by hand and answer GeneralMethods.success_response() (HTTP 200).


class EvalGroupListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True, allow_blank=True)
    created_at = serializers.DateTimeField()
    required_keys = serializers.ListField(
        child=serializers.CharField(),
        help_text="Union of the member templates' required mapping keys.",
    )
    evals_count = serializers.IntegerField()
    is_sample = serializers.BooleanField()


class EvalGroupListResultSerializer(serializers.Serializer):
    data = EvalGroupListItemSerializer(many=True)
    total_count = serializers.IntegerField()
    total_pages = serializers.IntegerField()


class EvalGroupListApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalGroupListResultSerializer()

    class Meta:
        ref_name = None


class EvalGroupMemberSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True, allow_blank=True)
    added_on = serializers.DateTimeField()
    added_by = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField())
    required_keys = serializers.ListField(child=serializers.CharField())
    optional_keys = serializers.ListField(child=serializers.CharField())
    models = serializers.ListField(child=serializers.CharField())


class EvalGroupFunctionParamTemplateSerializer(serializers.Serializer):
    eval_template_id = serializers.UUIDField()
    name = serializers.CharField()


class EvalGroupFunctionParamRequirementSerializer(serializers.Serializer):
    schema = JsonValueField(help_text="The function parameter's schema.")
    supported_by = EvalGroupFunctionParamTemplateSerializer(many=True)
    required_for = EvalGroupFunctionParamTemplateSerializer(many=True)


class EvalGroupDetailResultSerializer(serializers.Serializer):
    eval_group = EvalGroupSerializer(read_only=True)
    members = EvalGroupMemberSerializer(many=True)
    required_keys = serializers.ListField(child=serializers.CharField())
    models = serializers.ListField(
        child=serializers.CharField(),
        help_text="Models every member template supports.",
    )
    function_params_requirements = serializers.DictField(
        child=EvalGroupFunctionParamRequirementSerializer(),
        help_text="Function parameters keyed by name.",
    )


class EvalGroupDetailApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = EvalGroupDetailResultSerializer()

    class Meta:
        ref_name = None


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

"""API query contracts used by the evaluation and optimization lists."""

from rest_framework import serializers

from model_hub.serializers.pagination import PageSizeField
from tfc.utils.pagination import MAX_PAGE_SIZE


class DatasetEvaluationsQuerySerializer(serializers.Serializer):
    eval_type = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Use user to list evaluations attached to this dataset, including their runnable IDs.",
    )
    search_text = serializers.CharField(required=False, allow_blank=True)
    eval_categories = serializers.CharField(required=False, allow_blank=True)
    eval_tags = serializers.ListField(child=serializers.CharField(), required=False)
    use_cases = serializers.ListField(child=serializers.CharField(), required=False)
    experiment_id = serializers.UUIDField(required=False)
    order = serializers.CharField(required=False, allow_blank=True)


class EvalGroupQuerySerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)


class EvalGroupListQuerySerializer(EvalGroupQuerySerializer):
    page_number = serializers.IntegerField(min_value=0, default=0)
    page_size = PageSizeField(min_value=1, default=10, max_value=MAX_PAGE_SIZE)


class OptimizationListQuerySerializer(serializers.Serializer):
    dataset_id = serializers.UUIDField(required=False)
    column_id = serializers.UUIDField(required=False)
    develop_id = serializers.UUIDField(required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    # Keep the existing paginator behavior through the shared security ceiling.
    limit = PageSizeField(min_value=1, required=False, max_value=MAX_PAGE_SIZE)

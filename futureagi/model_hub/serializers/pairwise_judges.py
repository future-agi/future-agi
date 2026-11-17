from rest_framework import serializers

from model_hub.serializers.pairwise_preferences import PairwiseRunsSerializer
from tfc.utils.api_serializers import StrictInputSerializer


class PairwiseJudgeCreateSerializer(PairwiseRunsSerializer):
    judge_version = serializers.UUIDField()


class PairwiseJudgePageSerializer(StrictInputSerializer):
    offset = serializers.IntegerField(min_value=0, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=50)

    class Meta:
        ref_name = None


class PairwiseJudgeListQuerySerializer(
    PairwiseJudgePageSerializer, PairwiseRunsSerializer
):
    pass


class PairwiseJudgeCancelSerializer(StrictInputSerializer):
    class Meta:
        ref_name = None


class PairwiseJudgeItemSerializer(serializers.Serializer):
    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField()
    status = serializers.CharField()
    winner = serializers.CharField(allow_blank=True)
    reason = serializers.CharField(allow_blank=True)
    error = serializers.CharField(allow_blank=True)
    log_id = serializers.CharField(allow_blank=True)
    presentation_order = serializers.ChoiceField(choices=["ab", "ba"])
    finished_at = serializers.DateTimeField(allow_null=True)

    class Meta:
        ref_name = None


class PairwiseJudgeJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    dataset_version = serializers.UUIDField()
    source = serializers.ChoiceField(choices=["judge"])
    run_a = serializers.UUIDField()
    run_b = serializers.UUIDField()
    judge_version = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=["queued", "running", "completed", "failed", "cancelled"]
    )
    counts = serializers.DictField(child=serializers.IntegerField())
    total = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)
    items = PairwiseJudgeItemSerializer(many=True, required=False)
    next_offset = serializers.IntegerField(allow_null=True, required=False)

    class Meta:
        ref_name = None


class PairwiseJudgeListSerializer(serializers.Serializer):
    jobs = PairwiseJudgeJobSerializer(many=True)
    next_offset = serializers.IntegerField(allow_null=True)

    class Meta:
        ref_name = None

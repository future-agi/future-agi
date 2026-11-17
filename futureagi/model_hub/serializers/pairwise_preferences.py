from rest_framework import serializers

from tfc.utils.api_serializers import StrictInputSerializer


class PairwiseRunsSerializer(StrictInputSerializer):
    run_a = serializers.UUIDField()
    run_b = serializers.UUIDField()

    class Meta:
        ref_name = None
        swagger_schema_fields = {"additionalProperties": False}


class PairwisePreferenceSerializer(PairwiseRunsSerializer):
    row_id = serializers.UUIDField(help_text="Stable dataset-version example_id.")
    winner = serializers.ChoiceField(choices=["a", "b", "tie"])
    note = serializers.CharField(max_length=4000, allow_blank=True, default="")


class PairwiseSummaryQuerySerializer(PairwiseRunsSerializer):
    judge_job = serializers.UUIDField(required=False)


class PairwiseAgreementSerializer(serializers.Serializer):
    matches = serializers.IntegerField()
    compared = serializers.IntegerField()
    rate = serializers.FloatField(allow_null=True)

    class Meta:
        ref_name = None


class PairwiseSummarySerializer(serializers.Serializer):
    a_wins = serializers.IntegerField()
    b_wins = serializers.IntegerField()
    ties = serializers.IntegerField()
    rows_reviewed = serializers.IntegerField(help_text="Distinct reviewed rows.")
    rows_total = serializers.IntegerField(help_text="Rows shared by both versions.")
    human = serializers.DictField(child=serializers.IntegerField(), required=False)
    judge = serializers.DictField(child=serializers.IntegerField(), required=False)
    agreement = PairwiseAgreementSerializer(required=False)

    class Meta:
        ref_name = None

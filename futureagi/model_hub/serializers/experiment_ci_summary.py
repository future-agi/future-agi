from rest_framework import serializers


class ExperimentCISummaryQuerySerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    max_regressed = serializers.IntegerField(min_value=0, default=0)


class EvaluatorCISummarySerializer(serializers.Serializer):
    improved = serializers.IntegerField(min_value=0)
    regressed = serializers.IntegerField(min_value=0)
    unchanged = serializers.IntegerField(min_value=0)
    not_comparable = serializers.IntegerField(min_value=0)
    mean = serializers.FloatField(allow_null=True)
    baseline_mean = serializers.FloatField(allow_null=True)

    class Meta:
        ref_name = None


class ExperimentCISummarySerializer(serializers.Serializer):
    experiment_id = serializers.UUIDField()
    run_id = serializers.UUIDField()
    baseline_run_id = serializers.UUIDField()
    evaluators = serializers.DictField(child=EvaluatorCISummarySerializer())
    regressed_total = serializers.IntegerField(min_value=0)
    passed = serializers.BooleanField()

    class Meta:
        ref_name = None


class ExperimentCICommentQuerySerializer(ExperimentCISummaryQuerySerializer):
    baseline_run_id = serializers.UUIDField(required=False)
    top_n = serializers.IntegerField(min_value=1, max_value=20, default=5)


class ExperimentCICommentSerializer(serializers.Serializer):
    body = serializers.CharField(trim_whitespace=False)
    passed = serializers.BooleanField()
    results_path = serializers.CharField()

    class Meta:
        ref_name = None

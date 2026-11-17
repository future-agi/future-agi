"""Query and response contracts of a dataset row's runs across experiments."""

from rest_framework import serializers

from model_hub.serializers.results_runs import StrictQuerySerializer


class DatasetRowRunsQuerySerializer(StrictQuerySerializer):
    scope = serializers.ChoiceField(
        choices=["all", "current_version", "baseline_latest"], default="all"
    )
    sort = serializers.ChoiceField(
        choices=["newest", "worst", "best"], default="newest"
    )
    # Sent by the page when it is read as of a version: a row deleted later
    # still has its runs.
    version = serializers.IntegerField(required=False, min_value=1)


class DatasetRowRunsFlagsSerializer(serializers.Serializer):
    hard_case = serializers.BooleanField()
    fails_of_runs = serializers.ListField(
        child=serializers.IntegerField(), allow_null=True, min_length=2, max_length=2
    )
    flaky = serializers.BooleanField()
    expected_changed_after_runs = serializers.IntegerField()


class DatasetRowRunsSummarySerializer(serializers.Serializer):
    runs = serializers.IntegerField()
    experiments = serializers.IntegerField()
    primary_metric_name = serializers.CharField(allow_null=True)
    primary_scored = serializers.IntegerField()
    primary_pass = serializers.IntegerField()
    # The dataset's experiments with no recorded run (classic experiments):
    # counted, never listed, since their results cannot be traced to a row.
    classic_experiments = serializers.IntegerField()
    flags = DatasetRowRunsFlagsSerializer()


class DatasetRowRunsMetricSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    output_type = serializers.CharField(allow_null=True)
    higher_is_better = serializers.BooleanField(allow_null=True)
    pass_threshold = serializers.FloatField(allow_null=True)


class DatasetRowRunsEvalSerializer(serializers.Serializer):
    value = serializers.FloatField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)
    n = serializers.IntegerField()
    status = serializers.CharField()
    std = serializers.FloatField()
    flaky = serializers.BooleanField()


class DatasetRowRunsTrialSerializer(serializers.Serializer):
    trial_number = serializers.IntegerField()
    status = serializers.CharField()
    trace_id = serializers.CharField(allow_null=True)
    output = serializers.JSONField(allow_null=True)
    error = serializers.JSONField(allow_null=True)
    scores = serializers.DictField(child=serializers.JSONField())


class DatasetRowRunsRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    example_id = serializers.UUIDField()
    label = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)
    experiment_id = serializers.UUIDField(allow_null=True)
    experiment_name = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    output = serializers.JSONField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    trials = serializers.IntegerField()
    evals = serializers.DictField(child=DatasetRowRunsEvalSerializer())
    latency_ms = serializers.FloatField(allow_null=True)
    cost = serializers.FloatField(allow_null=True)
    ran_at = serializers.DateTimeField()
    dataset_version = serializers.DictField()
    is_dataset_baseline = serializers.BooleanField()
    row_changed_after = serializers.BooleanField()
    target_type = serializers.CharField()
    trace_id = serializers.CharField(allow_null=True)
    trials_detail = DatasetRowRunsTrialSerializer(many=True)


class DatasetRowRunsResultSerializer(serializers.Serializer):
    summary = DatasetRowRunsSummarySerializer()
    metrics = DatasetRowRunsMetricSerializer(many=True)
    runs = DatasetRowRunsRunSerializer(many=True)


class DatasetRowRunsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetRowRunsResultSerializer()

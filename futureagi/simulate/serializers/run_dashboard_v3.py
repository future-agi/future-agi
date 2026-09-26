"""Response contracts for simulation dashboard widgets."""

from rest_framework import serializers


class RunDashboardMetricSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    value = serializers.FloatField(allow_null=True)
    unit = serializers.ChoiceField(
        choices=["number", "percent", "ms", "seconds", "ratio", "cents"]
    )
    measured = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField()
    note = serializers.CharField(allow_blank=True)


class RunDashboardSegmentSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField()
    share = serializers.FloatField()
    statuses = serializers.ListField(child=serializers.CharField(), required=False)


class RunDashboardBreakdownSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    total = serializers.IntegerField()
    segments = RunDashboardSegmentSerializer(many=True)
    headline = RunDashboardSegmentSerializer(allow_null=True)


class RunDashboardDistributionSerializer(serializers.Serializer):
    key = serializers.CharField()
    average = serializers.FloatField(allow_null=True)
    measured = serializers.IntegerField()
    max = serializers.FloatField(allow_null=True)
    p50 = serializers.FloatField(allow_null=True)
    p90 = serializers.FloatField(allow_null=True)
    p99 = serializers.FloatField(allow_null=True)


class RunDashboardSloSerializer(RunDashboardDistributionSerializer):
    label = serializers.CharField()


class RunDashboardInterruptionsSerializer(serializers.Serializer):
    total = serializers.IntegerField(allow_null=True)
    measured = serializers.IntegerField()
    average = serializers.FloatField(allow_null=True)


class RunDashboardSeriesSerializer(serializers.Serializer):
    label = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    calls = serializers.IntegerField()
    duration_ms = serializers.FloatField(allow_null=True)
    llm_cents = serializers.FloatField(allow_null=True)
    tts_cents = serializers.FloatField(allow_null=True)
    stt_cents = serializers.FloatField(allow_null=True)
    storage_cents = serializers.FloatField(allow_null=True)


class RunDashboardPercentileSerializer(serializers.Serializer):
    percentile = serializers.IntegerField()
    value = serializers.FloatField(allow_null=True)


class RunDashboardHistogramBinSerializer(serializers.Serializer):
    label = serializers.CharField()
    lower = serializers.FloatField()
    upper = serializers.FloatField()
    count = serializers.IntegerField()
    danger = serializers.BooleanField()


class RunDashboardAgreementSerializer(serializers.Serializer):
    compared = serializers.IntegerField()
    agreed = serializers.IntegerField()
    percent = serializers.FloatField(allow_null=True)


class RunDashboardCsatSerializer(serializers.Serializer):
    bins = RunDashboardHistogramBinSerializer(many=True)
    measured = serializers.IntegerField()
    total = serializers.IntegerField()
    agreement = RunDashboardAgreementSerializer()


class RunDashboardResponseTimeSerializer(serializers.Serializer):
    bins = RunDashboardHistogramBinSerializer(many=True)
    measured = serializers.IntegerField()
    total = serializers.IntegerField()
    target_ms = serializers.IntegerField()
    p50 = serializers.FloatField(allow_null=True)
    p95 = serializers.FloatField(allow_null=True)
    at_or_above_target = serializers.IntegerField()
    at_or_above_target_percent = serializers.FloatField(allow_null=True)


class RunDashboardPipelineCostSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    total_cents = serializers.FloatField(allow_null=True)
    share = serializers.FloatField(allow_null=True)


class RunDashboardToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    invocations = serializers.IntegerField()
    measured = serializers.IntegerField()
    failures = serializers.IntegerField()
    failure_rate = serializers.FloatField(allow_null=True)
    failure_label = serializers.CharField()


class RunDashboardToolsSerializer(serializers.Serializer):
    total_invocations = serializers.IntegerField()
    total_tools = serializers.IntegerField()
    volume = RunDashboardToolSerializer(many=True)
    failures = RunDashboardToolSerializer(many=True)


class RunDashboardTaskSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    id = serializers.UUIDField()
    label = serializers.CharField()
    axis_label = serializers.CharField()
    value = serializers.FloatField()
    modality = serializers.CharField(allow_null=True)
    provider = serializers.CharField(allow_null=True)


class RunDashboardUnavailableSerializer(serializers.Serializer):
    key = serializers.CharField()
    reason = serializers.CharField()


class RunDashboardEvaluationSummarySerializer(serializers.Serializer):
    graders = serializers.IntegerField()
    passed = serializers.IntegerField()
    measured = serializers.IntegerField()
    pass_rate = serializers.FloatField(allow_null=True)


class RunDashboardRiskSerializer(serializers.Serializer):
    goal = serializers.CharField()
    passed = serializers.IntegerField()
    failed = serializers.IntegerField()
    error = serializers.IntegerField()
    inconclusive = serializers.IntegerField()


class RunDashboardV3Serializer(serializers.Serializer):
    metrics = RunDashboardMetricSerializer(many=True)
    breakdowns = RunDashboardBreakdownSerializer(many=True)
    voice_slos = RunDashboardSloSerializer(many=True)
    interruptions = RunDashboardInterruptionsSerializer()
    series = RunDashboardSeriesSerializer(many=True)
    series_limit = serializers.IntegerField()
    series_mode = serializers.ChoiceField(choices=["calls", "time_buckets"])
    latency_percentiles = RunDashboardPercentileSerializer(many=True)
    distributions = RunDashboardDistributionSerializer(many=True)
    csat = RunDashboardCsatSerializer()
    agent_response_time = RunDashboardResponseTimeSerializer()
    pipeline_cost = RunDashboardPipelineCostSerializer(many=True)
    tools = RunDashboardToolsSerializer()
    slowest_tasks = RunDashboardTaskSerializer(many=True)
    most_expensive_tasks = RunDashboardTaskSerializer(many=True)
    unavailable_features = RunDashboardUnavailableSerializer(many=True)
    evaluation_summary = RunDashboardEvaluationSummarySerializer()
    use_case_risk = RunDashboardRiskSerializer(many=True)
    goal_count = serializers.IntegerField()

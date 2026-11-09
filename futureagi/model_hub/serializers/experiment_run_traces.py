"""Contracts of the run-to-traces read (Ruling 95): one run's traces, joined by run id."""

from rest_framework import serializers

from model_hub.services.experiment_run_traces import DEFAULT_LIMIT, MAX_LIMIT


class ExperimentRunTracesQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(
        default=DEFAULT_LIMIT, min_value=1, max_value=MAX_LIMIT
    )
    offset = serializers.IntegerField(default=0, min_value=0, max_value=2147483647)


class _RunTraceBaseSerializer(serializers.Serializer):
    trace_id = serializers.CharField()
    root_span_id = serializers.CharField(allow_null=True)
    example_id = serializers.UUIDField()
    trial_number = serializers.IntegerField()
    item_status = serializers.CharField()
    trace_run_id = serializers.CharField(
        allow_null=True,
        help_text="The run id the trace's root span carries (fi.experiment.run_id).",
    )


class ExperimentRunTraceSerializer(_RunTraceBaseSerializer):
    project_id = serializers.UUIDField(allow_null=True)
    root_span_name = serializers.CharField(allow_null=True)
    start_time = serializers.CharField(allow_null=True)
    span_count = serializers.IntegerField()
    trace_example_id = serializers.CharField(allow_null=True)
    trace_trial = serializers.IntegerField(allow_null=True)


class ExperimentRunTraceNotJoinedSerializer(_RunTraceBaseSerializer):
    reason = serializers.ChoiceField(
        choices=["not_found", "other_run", "no_run_id"],
        help_text="not_found: the span store has no such trace in the caller's projects (yet); other_run: its root "
        "span names another run; no_run_id: its root span names no run.",
    )


class ExperimentRunTracesResultSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    traces = ExperimentRunTraceSerializer(many=True)
    not_joined = ExperimentRunTraceNotJoinedSerializer(many=True)
    items_with_trace = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    has_more = serializers.BooleanField()


class ExperimentRunTracesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ExperimentRunTracesResultSerializer()

"""
DRF serializers for the Error Feed API.

Two roles:
1. Input: validate query params / PATCH body (no Django model backing).
2. Output: serialize feed_types dataclass instances to JSON (via attribute lookup).
   DRF CamelCaseRenderer converts snake_case keys → camelCase automatically.
"""

from rest_framework import serializers

from tracer.models.trace_error_analysis import ClusterSource, FeedIssueStatus

# ---------------------------------------------------------------------------
# Allowed enum values (for input validation)
# ---------------------------------------------------------------------------

SEVERITY_CHOICES = ("critical", "high", "medium", "low")
SORT_BY_CHOICES = (
    "last_seen",
    "first_seen",
    "error_count",
    "unique_traces",
    "severity",
)
SORT_DIR_CHOICES = ("asc", "desc")


# ---------------------------------------------------------------------------
# Input serializers
# ---------------------------------------------------------------------------


class FeedListQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/."""

    project_id = serializers.UUIDField(required=False)
    search = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=FeedIssueStatus.choices, required=False)
    severity = serializers.ChoiceField(choices=SEVERITY_CHOICES, required=False)
    fix_layer = serializers.CharField(required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=ClusterSource.choices, required=False)
    issue_group = serializers.CharField(required=False, allow_blank=True)
    time_range_days = serializers.IntegerField(required=False, min_value=1)
    sort_by = serializers.ChoiceField(
        choices=SORT_BY_CHOICES, required=False, default="last_seen"
    )
    sort_dir = serializers.ChoiceField(
        choices=SORT_DIR_CHOICES, required=False, default="desc"
    )
    limit = serializers.IntegerField(
        required=False, default=25, min_value=1, max_value=200
    )
    offset = serializers.IntegerField(required=False, default=0, min_value=0)


class FeedStatsQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/stats/."""

    project_id = serializers.UUIDField(required=False)
    time_range_days = serializers.IntegerField(required=False, min_value=1)


class FeedDetailQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/{cluster_id}/."""

    project_id = serializers.UUIDField(required=False)


class FeedUpdateBodySerializer(serializers.Serializer):
    """Body for PATCH /tracer/feed/issues/{cluster_id}/."""

    project_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=FeedIssueStatus.choices, required=False)
    severity = serializers.ChoiceField(choices=SEVERITY_CHOICES, required=False)
    assignee = serializers.EmailField(required=False, allow_null=True)


# ---------------------------------------------------------------------------
# Output serializers (dataclass → JSON)
# ---------------------------------------------------------------------------


class ErrorNameSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField(allow_blank=True)


class TrendPointSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    value = serializers.IntegerField()
    users = serializers.IntegerField()


class FeedListRowSerializer(serializers.Serializer):
    cluster_id = serializers.CharField()
    source = serializers.CharField()
    modality = serializers.CharField()
    error = ErrorNameSerializer()
    status = serializers.CharField()
    severity = serializers.CharField()
    occurrences = serializers.IntegerField()
    trace_count = serializers.IntegerField()
    fix_layer = serializers.CharField(allow_null=True)
    users_affected = serializers.IntegerField()
    sessions = serializers.IntegerField()
    first_seen = serializers.DateTimeField(allow_null=True)
    last_seen = serializers.DateTimeField(allow_null=True)
    trends = TrendPointSerializer(many=True)
    assignees = serializers.ListField(child=serializers.CharField())
    model = serializers.CharField(allow_null=True)
    model_version = serializers.CharField(allow_null=True)
    project = serializers.CharField(allow_null=True)
    project_id = serializers.CharField(allow_null=True)
    environment = serializers.CharField(allow_null=True)
    eval_score = serializers.FloatField(allow_null=True)
    trace_id = serializers.CharField(allow_null=True)
    external_issue_url = serializers.CharField(allow_null=True)
    external_issue_id = serializers.CharField(allow_null=True)


class FeedListResponseSerializer(serializers.Serializer):
    data = FeedListRowSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class FeedListApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = FeedListResponseSerializer()


class FeedStatsSerializer(serializers.Serializer):
    total_errors = serializers.IntegerField()
    escalating = serializers.IntegerField()
    for_review = serializers.IntegerField()
    acknowledged = serializers.IntegerField()
    resolved = serializers.IntegerField()
    affected_users = serializers.IntegerField()


class FeedStatsApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = FeedStatsSerializer()


class TracePreviewSerializer(serializers.Serializer):
    trace_id = serializers.CharField()
    input = serializers.CharField(allow_null=True)
    output = serializers.CharField(allow_null=True)


class RcaTrailStepSerializer(serializers.Serializer):
    """One ``rca_trace`` frame — a union keyed on ``type`` (reasoning /
    step_start / step_result / synthesis). Fields are the FE-consumed contract
    (clusterAnalyzeSocket buildMessagesFromFrames). ``args``/``result`` are open
    tool-dependent maps → JSONField, not a nested serializer.
    """

    type = serializers.CharField()
    text = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    call_id = serializers.CharField(required=False, allow_null=True)
    tool = serializers.CharField(required=False, allow_null=True)
    args = serializers.JSONField(required=False)
    result = serializers.JSONField(required=False)
    synthesis = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    fix = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    confidence = serializers.CharField(required=False, allow_null=True)


class RcaSummarySerializer(serializers.Serializer):
    synthesis = serializers.CharField(allow_null=True, required=False)
    fix = serializers.CharField(allow_null=True, required=False)
    confidence = serializers.CharField(allow_null=True, required=False)
    evidence_trace_ids = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    analyzed_at = serializers.DateTimeField(allow_null=True, required=False)
    failures_at_run = serializers.IntegerField(allow_null=True, required=False)
    trace = RcaTrailStepSerializer(many=True, required=False)


class FeedDetailCoreSerializer(serializers.Serializer):
    row = FeedListRowSerializer()
    description = serializers.CharField(allow_null=True)
    success_trace = TracePreviewSerializer(allow_null=True)
    representative_trace = TracePreviewSerializer(allow_null=True)
    rca = RcaSummarySerializer(allow_null=True, required=False)


class FeedDetailApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = FeedDetailCoreSerializer()


# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------


class EventsOverTimePointSerializer(serializers.Serializer):
    date = serializers.CharField()
    errors = serializers.IntegerField()
    passing = serializers.IntegerField()
    users = serializers.IntegerField()


class KeyMomentSerializer(serializers.Serializer):
    kevinified = serializers.CharField()
    verbatim = serializers.CharField(allow_blank=True)


class PatternInsightEvidenceSerializer(serializers.Serializer):
    """Stat evidence for an insight tooltip. Union across the five insight
    builders — every key any builder emits must be declared or DRF drops it.
    All optional; each insight populates only its subset.
    """

    # strings
    test = serializers.CharField(required=False)
    baseline = serializers.CharField(required=False)
    tool = serializers.CharField(required=False)
    # floats
    z = serializers.FloatField(required=False)
    p_value = serializers.FloatField(required=False)
    ks_stat = serializers.FloatField(required=False)
    fail_median = serializers.FloatField(required=False)
    baseline_median = serializers.FloatField(required=False)
    # ints
    fail_pct = serializers.IntegerField(required=False)
    baseline_pct = serializers.IntegerField(required=False)
    hits = serializers.IntegerField(required=False)
    total = serializers.IntegerField(required=False)
    missing_in = serializers.IntegerField(required=False)
    traces_with_tools = serializers.IntegerField(required=False)


class PatternInsightSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, default="")
    value = serializers.CharField()
    # Caption supports **bold** markers, rendered by the FE renderRichCaption.
    caption = serializers.CharField(allow_blank=True)
    # Stat rigor for a future hover tooltip (test, z/p, lift, sample sizes).
    evidence = PatternInsightEvidenceSerializer(required=False)


class PatternSummarySerializer(serializers.Serializer):
    insights = PatternInsightSerializer(many=True)
    key_moments = KeyMomentSerializer(many=True)


class TraceSummarySerializer(serializers.Serializer):
    eval_score = serializers.FloatField(allow_null=True)
    latency_ms = serializers.IntegerField(allow_null=True)
    turns = serializers.IntegerField(allow_null=True)
    model = serializers.CharField(allow_null=True)
    input_tokens = serializers.IntegerField(allow_null=True)
    output_tokens = serializers.IntegerField(allow_null=True)


class TraceEvidenceSerializer(serializers.Serializer):
    input = serializers.CharField(allow_null=True)
    output = serializers.CharField(allow_null=True)
    fail_reel = serializers.ListField(child=serializers.DictField())
    pass_reel = serializers.ListField(child=serializers.DictField())
    judge_reason = serializers.CharField(allow_null=True, required=False)
    score = serializers.FloatField(allow_null=True, required=False)


class AgentFlowGraphSerializer(serializers.Serializer):
    nodes = serializers.ListField(child=serializers.DictField())
    edges = serializers.ListField(child=serializers.DictField())


class RepresentativeTraceSerializer(serializers.Serializer):
    id = serializers.CharField()
    status = serializers.CharField()
    timestamp = serializers.DateTimeField(allow_null=True)
    summary = TraceSummarySerializer()
    evidence = TraceEvidenceSerializer()
    agent_flow = AgentFlowGraphSerializer()
    root_causes = serializers.ListField(child=serializers.DictField())
    recommendations = serializers.ListField(child=serializers.DictField())
    what_changed = serializers.DictField(allow_null=True)


class OverviewResponseSerializer(serializers.Serializer):
    events_over_time = EventsOverTimePointSerializer(many=True)
    pattern_summary = PatternSummarySerializer()
    representative_traces = RepresentativeTraceSerializer(many=True)
    representative_total = serializers.IntegerField(default=0)


class OverviewApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = OverviewResponseSerializer()


# ---------------------------------------------------------------------------
# Traces tab
# ---------------------------------------------------------------------------


class TracesAggregatesSerializer(serializers.Serializer):
    total_traces = serializers.IntegerField()
    failing_traces = serializers.IntegerField()
    passing_traces = serializers.IntegerField()
    avg_score = serializers.FloatField()
    p50_latency = serializers.IntegerField()
    p95_latency = serializers.IntegerField()
    avg_turns = serializers.FloatField()


class TracesListRowSerializer(serializers.Serializer):
    id = serializers.CharField()
    input = serializers.CharField(allow_null=True)
    timestamp = serializers.DateTimeField(allow_null=True)
    latency_ms = serializers.IntegerField(allow_null=True)
    tokens = serializers.IntegerField(allow_null=True)
    cost = serializers.FloatField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    turns = serializers.IntegerField(allow_null=True)


class TracesTabResponseSerializer(serializers.Serializer):
    aggregates = TracesAggregatesSerializer()
    traces = TracesListRowSerializer(many=True)
    total = serializers.IntegerField()


class TracesTabApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TracesTabResponseSerializer()


class TracesTabQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/{cluster_id}/traces/."""

    limit = serializers.IntegerField(
        required=False, default=50, min_value=1, max_value=500
    )
    offset = serializers.IntegerField(required=False, default=0, min_value=0)


class OverviewQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/{cluster_id}/overview/."""

    rep_limit = serializers.IntegerField(
        required=False, default=20, min_value=1, max_value=200
    )


# ---------------------------------------------------------------------------
# Trends tab
# ---------------------------------------------------------------------------


class TrendMetricSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.CharField()
    delta = serializers.FloatField()
    unit = serializers.CharField(allow_blank=True)


class ScoreTrendSerializer(serializers.Serializer):
    label = serializers.CharField()
    current = serializers.FloatField()
    prev = serializers.FloatField()
    sparkline = serializers.ListField(child=serializers.FloatField())


class HeatmapCellSerializer(serializers.Serializer):
    day = serializers.IntegerField()
    hour = serializers.IntegerField()
    value = serializers.IntegerField()


class TrendsTabResponseSerializer(serializers.Serializer):
    metrics = TrendMetricSerializer(many=True)
    events_over_time = EventsOverTimePointSerializer(many=True)
    score_trends = ScoreTrendSerializer(many=True)
    activity_heatmap = serializers.ListField(
        child=HeatmapCellSerializer(many=True),
    )


class TrendsTabApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = TrendsTabResponseSerializer()


class TrendsTabQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/{cluster_id}/trends/."""

    days = serializers.IntegerField(
        required=False, default=14, min_value=1, max_value=90
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


class SidebarTimelineSerializer(serializers.Serializer):
    first_seen = serializers.DateTimeField(allow_null=True)
    last_seen = serializers.DateTimeField(allow_null=True)
    age_days = serializers.IntegerField(allow_null=True)


class SidebarAIMetadataSerializer(serializers.Serializer):
    model = serializers.CharField(allow_null=True)
    model_version = serializers.CharField(allow_null=True)
    project = serializers.CharField(allow_null=True)
    eval_score = serializers.FloatField(allow_null=True)
    trace_id = serializers.CharField(allow_null=True)


class EvaluationResultSerializer(serializers.Serializer):
    label = serializers.CharField()
    type = serializers.CharField()
    result = serializers.CharField()
    score = serializers.FloatField(allow_null=True)
    value = serializers.CharField(allow_null=True)


class CoOccurringIssueSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    type = serializers.CharField(allow_blank=True)
    co_occurrence = serializers.FloatField()
    count = serializers.IntegerField()
    severity = serializers.CharField()


class FeedSidebarSerializer(serializers.Serializer):
    timeline = SidebarTimelineSerializer()
    ai_metadata = SidebarAIMetadataSerializer()
    evaluations = EvaluationResultSerializer(many=True)
    co_occurring_issues = CoOccurringIssueSerializer(many=True)


class FeedSidebarApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = FeedSidebarSerializer()


class FeedSidebarQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/{cluster_id}/sidebar/."""

    trace_id = serializers.CharField(required=False, allow_blank=False)


# ---------------------------------------------------------------------------
# Deep analysis
# ---------------------------------------------------------------------------


class RootCauseSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()


class RecommendationSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    priority = serializers.CharField()
    root_cause_link = serializers.IntegerField(allow_null=True)
    immediate_fix = serializers.CharField(allow_null=True)
    insights = serializers.CharField(allow_null=True)
    evidence = serializers.ListField(child=serializers.CharField())


class DeepAnalysisResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    trace_id = serializers.CharField()
    root_causes = RootCauseSerializer(many=True)
    recommendations = RecommendationSerializer(many=True)
    immediate_fix = serializers.CharField(allow_null=True)


class DeepAnalysisApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DeepAnalysisResponseSerializer()


class DeepAnalysisDispatchResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    trace_id = serializers.CharField()


class DeepAnalysisDispatchApiResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = DeepAnalysisDispatchResponseSerializer()


class DeepAnalysisQuerySerializer(serializers.Serializer):
    """Query params for GET /tracer/feed/issues/{cluster_id}/root-cause/."""

    trace_id = serializers.CharField(required=True, allow_blank=False)


class DeepAnalysisBodySerializer(serializers.Serializer):
    """Body for POST /tracer/feed/issues/{cluster_id}/deep-analysis/."""

    trace_id = serializers.CharField(required=True, allow_blank=False)
    force = serializers.BooleanField(required=False, default=False)

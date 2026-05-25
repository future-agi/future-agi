"""
Typed dataclasses for the Error Feed API.

Single source of truth for all feed response shapes. Queries return these,
services orchestrate them, serializers translate them to JSON.

No raw dicts cross layer boundaries.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# ---------------------------------------------------------------------------
# Common / shared
# ---------------------------------------------------------------------------


@dataclass
class TrendPoint:
    """Single bucket in a cluster's trend sparkline."""

    timestamp: datetime
    value: int
    users: int = 0


@dataclass
class ErrorName:
    """Cluster error name/type pair, matches frontend `error: { name, type }`."""

    name: str
    type: str


# ---------------------------------------------------------------------------
# List endpoint (GET /tracer/feed/issues/)
# ---------------------------------------------------------------------------


@dataclass
class FeedListRow:
    """One row in the Error Feed table."""

    cluster_id: str
    source: str  # "scanner" | "eval"
    error: ErrorName
    status: str  # escalating | for_review | acknowledged | resolved
    severity: str  # critical | high | medium | low (mapped from Priority)
    occurrences: int  # error_count
    trace_count: int  # unique_traces
    fix_layer: Optional[str]
    users_affected: int
    sessions: int
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    trends: List[TrendPoint] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    model: Optional[str] = None
    model_version: Optional[str] = None
    project: Optional[str] = None
    project_id: Optional[str] = None
    project_source: Optional[str] = None
    environment: Optional[str] = None
    eval_score: Optional[float] = None
    trace_id: Optional[str] = None
    external_issue_url: Optional[str] = None
    external_issue_id: Optional[str] = None


@dataclass
class FeedListResponse:
    """Paginated list response."""

    data: List[FeedListRow]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Stats endpoint (GET /tracer/feed/issues/stats/)
# ---------------------------------------------------------------------------


@dataclass
class FeedStats:
    """Top stats bar totals."""

    total_errors: int
    escalating: int
    acknowledged: int
    for_review: int
    resolved: int
    affected_users: int


# ---------------------------------------------------------------------------
# Detail core endpoint (GET /tracer/feed/issues/{cluster_id}/)
# ---------------------------------------------------------------------------


@dataclass
class TracePreview:
    """Short trace summary used in detail core (success/representative)."""

    trace_id: str
    input: Optional[str] = None
    output: Optional[str] = None


@dataclass
class FeedDetailCore:
    """Detail view core payload — extends list row with trace previews."""

    row: FeedListRow
    description: Optional[str] = None
    success_trace: Optional[TracePreview] = None
    representative_trace: Optional[TracePreview] = None


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@dataclass
class FeedUpdatePayload:
    """Fields allowed on PATCH /tracer/feed/issues/{cluster_id}/"""

    status: Optional[str] = None
    severity: Optional[str] = None
    assignee: Optional[str] = None


# ---------------------------------------------------------------------------
# Overview tab endpoint (GET /tracer/feed/issues/{cluster_id}/overview/)
# ---------------------------------------------------------------------------


@dataclass
class EventsOverTimePoint:
    """Single daily bucket in the events-over-time chart."""

    date: str  # YYYY-MM-DD
    errors: int
    passing: int = 0
    users: int = 0


@dataclass
class KeyMoment:
    """Kevinified + verbatim pair from TraceScanResult.key_moments."""

    kevinified: str
    verbatim: str


@dataclass
class PatternInsight:
    """One cluster-level insight card in the Overview's Pattern Summary grid.

    ``value`` is the punchy metric ("3 / 21", "All", "4.2").
    ``caption`` is the descriptive text under it.
    """

    value: str
    caption: str


@dataclass
class PatternSummary:
    """Aggregate signal across the cluster's traces — adaptive insights."""

    insights: List[PatternInsight] = field(default_factory=list)
    key_moments: List[KeyMoment] = field(default_factory=list)


@dataclass
class TraceSummary:
    """Per-trace summary stats shown in the Overview tab trace list."""

    eval_score: Optional[float] = None
    latency_ms: Optional[int] = None
    turns: Optional[int] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class TraceEvidence:
    """Raw input/output + optional breadcrumb reels (populated later phases)."""

    input: Optional[str] = None
    output: Optional[str] = None
    fail_reel: List[dict] = field(default_factory=list)
    pass_reel: List[dict] = field(default_factory=list)


@dataclass
class AgentFlowGraph:
    """Placeholder for the state-graph diagram (nodes + edges filled later)."""

    nodes: List[dict] = field(default_factory=list)
    edges: List[dict] = field(default_factory=list)


@dataclass
class RepresentativeTrace:
    """Single trace in the Overview tab's "Traces affected" list."""

    id: str
    status: str  # "fail" | "pass"
    timestamp: Optional[datetime]
    summary: TraceSummary
    evidence: TraceEvidence
    # Frontend-crash-safe defaults; populated in later phases (Phase 4 deep
    # analysis + state graph later).
    agent_flow: AgentFlowGraph = field(default_factory=AgentFlowGraph)
    root_causes: List[dict] = field(default_factory=list)
    recommendations: List[dict] = field(default_factory=list)
    what_changed: Optional[dict] = None


@dataclass
class OverviewResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/overview/"""

    events_over_time: List[EventsOverTimePoint] = field(default_factory=list)
    pattern_summary: PatternSummary = field(default_factory=PatternSummary)
    representative_traces: List[RepresentativeTrace] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Traces tab endpoint (GET /tracer/feed/issues/{cluster_id}/traces/)
# ---------------------------------------------------------------------------


@dataclass
class TracesAggregates:
    """Stat bar at the top of the Traces tab."""

    total_traces: int = 0
    failing_traces: int = 0
    passing_traces: int = 0
    avg_score: float = 0.0
    p50_latency: int = 0
    p95_latency: int = 0
    avg_turns: float = 0.0


@dataclass
class TracesListRow:
    """Flat row in the Traces tab AG Grid."""

    id: str
    input: Optional[str]
    timestamp: Optional[datetime]
    latency_ms: Optional[int]
    tokens: Optional[int]
    cost: Optional[float]
    score: Optional[float]
    turns: Optional[int]


@dataclass
class TracesTabResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/traces/"""

    aggregates: TracesAggregates
    traces: List[TracesListRow] = field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Trends tab endpoint (GET /tracer/feed/issues/{cluster_id}/trends/)
# ---------------------------------------------------------------------------


@dataclass
class TrendMetric:
    """One of the three KPI cards at the top of the Trends tab."""

    label: str
    value: str  # pre-formatted ("92%", "0.31", "342")
    delta: float  # signed — frontend colors based on sign
    unit: str = ""


@dataclass
class ScoreTrend:
    """Score sparkline for a single CustomEvalConfig across the window."""

    label: str  # CustomEvalConfig.name
    current: float  # avg over current window
    prev: float  # avg over previous window
    sparkline: List[float] = field(default_factory=list)


@dataclass
class HeatmapCell:
    """One cell in the 7×24 Activity Heatmap (day 0=Sun … 6=Sat)."""

    day: int
    hour: int
    value: int


@dataclass
class TrendsTabResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/trends/"""

    metrics: List[TrendMetric] = field(default_factory=list)
    events_over_time: List[EventsOverTimePoint] = field(default_factory=list)
    score_trends: List[ScoreTrend] = field(default_factory=list)
    activity_heatmap: List[List[HeatmapCell]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sidebar endpoint (GET /tracer/feed/issues/{cluster_id}/sidebar/)
# ---------------------------------------------------------------------------


@dataclass
class SidebarTimeline:
    """Timeline section — just the basics (no deploy, no audit log)."""

    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    age_days: Optional[int]


@dataclass
class SidebarAIMetadata:
    """AI Metadata section — the 5 fields that actually have backend data."""

    model: Optional[str] = None
    model_version: Optional[str] = None
    project: Optional[str] = None
    eval_score: Optional[float] = None
    trace_id: Optional[str] = None


@dataclass
class EvaluationResult:
    """One evaluation row in the sidebar.

    ``type`` is ``"llm_judge"`` when the eval's CustomEvalConfig.model starts
    with ``turing_``, otherwise ``"deterministic"``. ``score`` is populated
    for llm_judge evals, ``value`` for deterministic ones.
    """

    label: str  # CustomEvalConfig.name
    type: str  # "llm_judge" | "deterministic"
    result: str  # "passed" | "failed"
    score: Optional[float] = None
    value: Optional[str] = None


@dataclass
class CoOccurringIssue:
    """Another cluster whose traces overlap with this one."""

    id: str  # cluster_id
    title: str
    type: str  # issue_category
    co_occurrence: float  # Jaccard 0..1
    count: int  # number of shared traces
    severity: str


@dataclass
class FeedSidebar:
    """Payload for GET /tracer/feed/issues/{cluster_id}/sidebar/"""

    timeline: SidebarTimeline
    ai_metadata: SidebarAIMetadata
    evaluations: List[EvaluationResult] = field(default_factory=list)
    co_occurring_issues: List[CoOccurringIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Deep analysis endpoints
# ---------------------------------------------------------------------------


@dataclass
class RootCause:
    """One root cause in the deep analysis result. Aggregated across all
    TraceErrorDetail rows for the trace, deduped, and ranked."""

    rank: int
    title: str
    description: str


@dataclass
class Recommendation:
    """One recommendation card. Derived from a single TraceErrorDetail row."""

    id: str  # error_id (e.g. "E001")
    title: str  # last segment of the error category path
    description: str
    priority: str  # critical | high | medium | low
    root_cause_link: Optional[int] = None  # index into root_causes (1-based)
    immediate_fix: Optional[str] = None
    insights: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


@dataclass
class DeepAnalysisResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/root-cause/.

    ``status`` drives the frontend button state:
    - ``idle``     — no analysis exists, button offers "Run Deep Analysis"
    - ``running``  — Temporal activity in flight, frontend polls
    - ``done``     — results populated, frontend renders the panel
    - ``failed``   — analysis errored out, button offers a retry
    """

    status: str
    trace_id: str
    root_causes: List[RootCause] = field(default_factory=list)
    recommendations: List[Recommendation] = field(default_factory=list)
    immediate_fix: Optional[str] = None


@dataclass
class DeepAnalysisDispatchResponse:
    """Payload for POST /tracer/feed/issues/{cluster_id}/deep-analysis/.

    ``status`` is one of:
    - ``running``  — dispatched (or already running for this trace)
    - ``done``     — cached result exists and force=False, nothing to do
    """

    status: str
    trace_id: str

"""
Typed dataclasses for the Error Feed API.

Single source of truth for all feed response shapes. Queries return these,
services orchestrate them, serializers translate them to JSON.

No raw dicts cross layer boundaries.
"""

from dataclasses import dataclass, field
from datetime import datetime

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
    fix_layer: str | None
    users_affected: int
    sessions: int
    first_seen: datetime | None
    last_seen: datetime | None
    trends: list[TrendPoint] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    # "voice" | "text" — inherited from the project (voice simulator agent),
    # decides the per-trace surface (call player vs text evidence) in the FE.
    modality: str = "text"
    model: str | None = None
    model_version: str | None = None
    project: str | None = None
    project_id: str | None = None
    environment: str | None = None
    eval_score: float | None = None
    trace_id: str | None = None
    external_issue_url: str | None = None
    external_issue_id: str | None = None


@dataclass
class FeedListResponse:
    """Paginated list response."""

    data: list[FeedListRow]
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
    input: str | None = None
    output: str | None = None


@dataclass
class RcaSummary:
    """Cached cluster-RCA result for the headline card (PRD §7.1).

    Populated from the last cluster-rca agent run. ``synthesis`` is None when
    the cluster has never been analyzed — the card shows its empty state then,
    not a fabricated summary. ``failures_at_run`` is the cluster's error_count
    at analysis time; the card compares it against the current count to show a
    "N new since last analysis" stale nudge.
    """

    synthesis: str | None = None
    fix: str | None = None
    confidence: str | None = None  # H | M | L
    evidence_trace_ids: list[str] = field(default_factory=list)
    analyzed_at: datetime | None = None
    failures_at_run: int | None = None
    # Investigation trail (reasoning + tool events), not an observability trace.
    # The Analyze tab replays the full run, not just the synthesis.
    trace: list[dict] = field(default_factory=list)


@dataclass
class FeedDetailCore:
    """Detail view core payload — extends list row with trace previews."""

    row: FeedListRow
    description: str | None = None
    success_trace: TracePreview | None = None
    representative_trace: TracePreview | None = None
    rca: RcaSummary | None = None


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@dataclass
class FeedUpdatePayload:
    """Fields allowed on PATCH /tracer/feed/issues/{cluster_id}/"""

    status: str | None = None
    severity: str | None = None
    assignee: str | None = None
    assignee_provided: bool = False


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
    """One effect-size insight card in the Overview Pattern Summary grid.

    Wire shape matches the final designer UI (``OverviewTab`` → PatternSummary):

    - ``title``   — small uppercase kicker ("Common failure phrase").
    - ``value``   — the punchy metric ("17 / 38", "82%", "~12.4s").
    - ``caption`` — one human sentence; the key term wrapped in ``**bold**``
      markers, which the FE ``renderRichCaption`` renders bold. Never contains
      z/p/lift/test-name — the stat machinery is the gate, not the message.

    ``effect`` is the normalized effect-size the adaptive picker ranks by (not
    serialized). ``evidence`` carries the stat rigor (test, z/p, lift, sample
    sizes) for a future hover tooltip — the current FE card has no tooltip, so
    it is unused for now but kept so the rigor isn't thrown away.
    """

    title: str
    value: str
    caption: str
    effect: float = 0.0
    evidence: dict = field(default_factory=dict)


@dataclass
class PatternSummary:
    """Aggregate signal across the cluster's traces — adaptive insights."""

    insights: list[PatternInsight] = field(default_factory=list)
    key_moments: list[KeyMoment] = field(default_factory=list)


@dataclass
class TraceSummary:
    """Per-trace summary stats shown in the Overview tab trace list."""

    eval_score: float | None = None
    latency_ms: int | None = None
    turns: int | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class TraceEvidence:
    """Raw input/output + breadcrumb reels + (eval clusters) the judge's
    reasoning and score the artifact was scored against."""

    input: str | None = None
    output: str | None = None
    fail_reel: list[dict] = field(default_factory=list)
    pass_reel: list[dict] = field(default_factory=list)
    judge_reason: str | None = None
    score: float | None = None


@dataclass
class AgentFlowGraph:
    """Placeholder for the state-graph diagram (nodes + edges filled later)."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)


@dataclass
class RepresentativeTrace:
    """Single trace in the Overview tab's "Traces affected" list."""

    id: str
    status: str  # "fail" | "pass"
    timestamp: datetime | None
    summary: TraceSummary
    evidence: TraceEvidence
    # Frontend-crash-safe defaults; populated in later phases (Phase 4 deep
    # analysis + state graph later).
    agent_flow: AgentFlowGraph = field(default_factory=AgentFlowGraph)
    root_causes: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    what_changed: dict | None = None


@dataclass
class OverviewResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/overview/"""

    events_over_time: list[EventsOverTimePoint] = field(default_factory=list)
    pattern_summary: PatternSummary = field(default_factory=PatternSummary)
    representative_traces: list[RepresentativeTrace] = field(default_factory=list)
    # Total members in the cluster — representative_traces is capped by
    # rep_limit, so the FE needs this to render "showing N of M".
    representative_total: int = 0


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
    input: str | None
    timestamp: datetime | None
    latency_ms: int | None
    tokens: int | None
    cost: float | None
    score: float | None
    turns: int | None


@dataclass
class TracesTabResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/traces/"""

    aggregates: TracesAggregates
    traces: list[TracesListRow] = field(default_factory=list)
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
    sparkline: list[float] = field(default_factory=list)


@dataclass
class HeatmapCell:
    """One cell in the 7×24 Activity Heatmap (day 0=Sun … 6=Sat)."""

    day: int
    hour: int
    value: int


@dataclass
class TrendsTabResponse:
    """Payload for GET /tracer/feed/issues/{cluster_id}/trends/"""

    metrics: list[TrendMetric] = field(default_factory=list)
    events_over_time: list[EventsOverTimePoint] = field(default_factory=list)
    score_trends: list[ScoreTrend] = field(default_factory=list)
    activity_heatmap: list[list[HeatmapCell]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sidebar endpoint (GET /tracer/feed/issues/{cluster_id}/sidebar/)
# ---------------------------------------------------------------------------


@dataclass
class SidebarTimeline:
    """Timeline section — just the basics (no deploy, no audit log)."""

    first_seen: datetime | None
    last_seen: datetime | None
    age_days: int | None


@dataclass
class SidebarAIMetadata:
    """AI Metadata section — the 5 fields that actually have backend data."""

    model: str | None = None
    model_version: str | None = None
    project: str | None = None
    eval_score: float | None = None
    trace_id: str | None = None


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
    score: float | None = None
    value: str | None = None


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
    evaluations: list[EvaluationResult] = field(default_factory=list)
    co_occurring_issues: list[CoOccurringIssue] = field(default_factory=list)


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
    root_cause_link: int | None = None  # index into root_causes (1-based)
    immediate_fix: str | None = None
    insights: str | None = None
    evidence: list[str] = field(default_factory=list)


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
    root_causes: list[RootCause] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    immediate_fix: str | None = None


@dataclass
class DeepAnalysisDispatchResponse:
    """Payload for POST /tracer/feed/issues/{cluster_id}/deep-analysis/.

    ``status`` is one of:
    - ``running``  — dispatched (or already running for this trace)
    - ``done``     — cached result exists and force=False, nothing to do
    """

    status: str
    trace_id: str

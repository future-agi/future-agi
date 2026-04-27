import uuid

from django.db import models

from accounts.models.user import User
from tfc.utils.base_model import BaseModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_scan import TraceScanIssue


class Priority(models.TextChoices):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    URGENT = "urgent"


class TraceErrorAnalysis(BaseModel):
    """Stores comprehensive error analysis results for traces"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="error_analyses",
        null=False,
        blank=False,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="error_analyses",
        null=False,
        blank=False,
    )

    # Analysis metadata
    analysis_date = models.DateTimeField(auto_now_add=True)
    agent_version = models.CharField(max_length=50, default="1.0")
    memory_enhanced = models.BooleanField(default=False)

    # Summary metrics
    overall_score = models.FloatField(null=True, blank=True)
    total_errors = models.IntegerField(default=0)
    high_impact_errors = models.IntegerField(default=0)
    medium_impact_errors = models.IntegerField(default=0)
    low_impact_errors = models.IntegerField(default=0)
    recommended_priority = models.CharField(max_length=20, default="LOW")

    # Detailed scores - updated to match new scoring categories
    factual_grounding_score = models.FloatField(null=True, blank=True)
    factual_grounding_reason = models.TextField(null=True, blank=True)

    privacy_and_safety_score = models.FloatField(null=True, blank=True)
    privacy_and_safety_reason = models.TextField(null=True, blank=True)

    instruction_adherence_score = models.FloatField(null=True, blank=True)
    instruction_adherence_reason = models.TextField(null=True, blank=True)

    optimal_plan_execution_score = models.FloatField(null=True, blank=True)
    optimal_plan_execution_reason = models.TextField(null=True, blank=True)

    # Insights and recommendations
    insights = models.TextField(null=True, blank=True)
    memory_context = models.JSONField(default=dict, blank=True)

    # Error grouping
    grouped_errors_count = models.IntegerField(default=0)

    class Meta:
        db_table = "tracer_trace_error_analysis"
        ordering = ["-analysis_date"]
        indexes = [
            models.Index(fields=["trace", "analysis_date"]),
            models.Index(fields=["project", "analysis_date"]),
            models.Index(fields=["overall_score"]),
            models.Index(fields=["recommended_priority"]),
            models.Index(fields=["total_errors"]),
        ]


class TraceErrorDetail(BaseModel):
    """Stores individual error details from analysis with new structure"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.ForeignKey(
        TraceErrorAnalysis,
        on_delete=models.CASCADE,
        related_name="error_details",
        null=False,
        blank=False,
    )

    # Error identification
    error_id = models.CharField(
        max_length=20, null=False, blank=False
    )  # E001, E002, etc.
    cluster_id = models.CharField(
        max_length=20, null=True, blank=True
    )  # C01, C02, etc.

    # Error classification - updated for new taxonomy structure
    category = models.CharField(
        max_length=200, null=False, blank=False
    )  # Full category path: "Thinking & Response Issues > Hallucination Errors > Hallucinated Content"

    # Error details
    impact: models.CharField = models.CharField(
        max_length=20, default="MEDIUM"
    )  # HIGH, MEDIUM, LOW
    urgency_to_fix = models.CharField(
        max_length=20, default="HIGH"
    )  # IMMEDIATE, HIGH, MEDIUM, LOW

    # Location and evidence
    location_spans = models.JSONField(default=list, blank=True)  # List of span IDs
    evidence_snippets = models.JSONField(
        default=list, blank=True
    )  # List of evidence texts
    description = models.TextField(null=True, blank=True)

    # Root causes and recommendations - new fields
    root_causes = models.JSONField(
        default=list, blank=True
    )  # List of root cause descriptions
    recommendation = models.TextField(null=True, blank=True)
    immediate_fix = models.TextField(null=True, blank=True)

    # Trace impact
    trace_impact = models.TextField(null=True, blank=True)
    trace_assessment = models.TextField(null=True, blank=True)

    # Analysis metadata
    llm_analysis = models.TextField(null=True, blank=True)
    memory_enhanced = models.BooleanField(default=False)

    class Meta:
        db_table = "tracer_trace_error_detail"
        ordering = ["error_id"]
        indexes = [
            models.Index(fields=["analysis", "error_id"]),
            models.Index(fields=["category"]),
            models.Index(fields=["impact"]),
            models.Index(fields=["urgency_to_fix"]),
            models.Index(fields=["cluster_id"]),
        ]


class FeedIssueStatus(models.TextChoices):
    ESCALATING = "escalating"
    FOR_REVIEW = "for_review"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ClusterSource(models.TextChoices):
    SCANNER = "scanner"
    EVAL = "eval"


class TraceErrorGroup(BaseModel):
    """Stores grouped error information — each row = one Feed issue."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="error_groups",
        null=False,
        blank=False,
        default=None,
    )

    # --- Feed fields (new) ---
    source = models.CharField(
        max_length=20,
        choices=ClusterSource.choices,
        default=ClusterSource.SCANNER,
    )
    issue_group = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Scanner: "Tool Failures", Eval: eval name',
    )
    issue_category = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Scanner: "Language-only", Eval: criteria or null',
    )
    fix_layer = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text='"Tools" / "Prompt" / "Orchestration" / "Guardrails"',
    )
    title = models.TextField(
        null=True,
        blank=True,
        help_text="Descriptive issue title",
    )
    status = models.CharField(
        max_length=20,
        choices=FeedIssueStatus.choices,
        default=FeedIssueStatus.ESCALATING,
    )
    eval_config = models.ForeignKey(
        CustomEvalConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="error_groups",
        help_text="For eval-sourced clusters",
    )
    success_trace = models.ForeignKey(
        Trace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="success_for_groups",
        help_text="Precomputed nearest success trace",
    )

    # --- Existing fields (backwards compat) ---
    cluster_id = models.CharField(max_length=20, null=False, blank=False)
    error_type = models.CharField(max_length=200, null=False, blank=False)

    total_events = models.IntegerField(default=0)
    unique_traces = models.IntegerField(default=0)
    unique_users = models.IntegerField(default=0)
    first_seen = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    error_ids = models.JSONField(default=list, blank=True)
    combined_impact = models.CharField(max_length=20, default="MEDIUM")
    combined_description = models.TextField(null=True, blank=True)
    error_count = models.IntegerField(default=0)
    trace_impact = models.TextField(null=True, blank=True)

    # Ticket Management
    assignee = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_error_groups",
    )
    priority = models.CharField(
        max_length=20, default=Priority.MEDIUM, choices=Priority.choices
    )
    external_issue_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text="URL of linked external issue (Linear, GitHub, Jira, etc.)",
    )
    external_issue_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="External issue identifier (e.g. TH-123, #456)",
    )

    class Meta:
        db_table = "tracer_trace_error_group"
        ordering = ["cluster_id"]
        indexes = [
            models.Index(fields=["project", "cluster_id"]),
            models.Index(fields=["error_type"]),
            models.Index(fields=["combined_impact"]),
            models.Index(fields=["project", "source"]),
            models.Index(fields=["project", "issue_group"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "cluster_id"],
                condition=models.Q(deleted=False),
                name="unique_project_cluster_if_not_deleted",
            ),
        ]


class AnalysisErrorGroup(BaseModel):
    """Stores error groups generated by the agent during single trace analysis

    This is different from TraceErrorGroup which is used for cross-trace clustering.
    This model stores the 'grouped_errors' that are generated during individual
    trace analysis by the agent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.ForeignKey(
        TraceErrorAnalysis,
        on_delete=models.CASCADE,
        related_name="analysis_error_groups",  # Different related_name
        null=False,
        blank=False,
    )

    # Group identification (within this single trace analysis)
    cluster_id = models.CharField(
        max_length=20, null=False, blank=False
    )  # C01, C02, etc.
    error_type = models.CharField(max_length=200, null=False, blank=False)

    # Group details
    error_ids = models.JSONField(
        default=list, blank=True
    )  # List of error IDs in this group
    affected_spans = models.JSONField(default=list, blank=True)  # List of span IDs
    combined_impact = models.CharField(max_length=20, default="MEDIUM")
    combined_description = models.TextField(null=True, blank=True)
    error_count = models.IntegerField(default=0)
    trace_impact = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "tracer_analysis_error_group"  # Different table
        ordering = ["cluster_id"]
        indexes = [
            models.Index(fields=["analysis", "cluster_id"]),
            models.Index(fields=["error_type"]),
            models.Index(fields=["combined_impact"]),
        ]


class ErrorPattern(BaseModel):
    """Stores learned error patterns for semantic memory with updated structure"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="error_patterns",
        null=False,
        blank=False,
    )

    # Pattern identification
    pattern_type = models.CharField(
        max_length=50, null=False, blank=False
    )  # tool_error, format_error, etc.
    category = models.CharField(
        max_length=200, null=False, blank=False
    )  # Full category path
    subcategory = models.CharField(max_length=100, null=False, blank=False)
    specific_error = models.CharField(
        max_length=100, null=True, blank=True
    )  # Specific error name

    # Pattern details
    pattern_description = models.TextField(null=True, blank=True)
    frequency = models.IntegerField(default=1)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    # Context information
    tool_name = models.CharField(max_length=100, null=True, blank=True)
    span_type = models.CharField(max_length=50, null=True, blank=True)
    input_pattern = models.TextField(null=True, blank=True)
    output_pattern = models.TextField(null=True, blank=True)

    # Recommendations and fixes - updated fields
    recommendation = models.TextField(null=True, blank=True)
    immediate_fix = models.TextField(null=True, blank=True)
    root_causes = models.JSONField(default=list, blank=True)  # List of root causes

    # Metadata
    confidence_score = models.FloatField(default=0.0)
    is_resolved = models.BooleanField(default=False)
    resolution_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_error_pattern"
        ordering = ["-frequency", "-last_seen"]
        indexes = [
            models.Index(fields=["project", "category"]),
            models.Index(fields=["pattern_type", "frequency"]),
            models.Index(fields=["tool_name"]),
            models.Index(fields=["is_resolved"]),
            models.Index(fields=["confidence_score"]),
        ]


class ErrorMemory(BaseModel):
    """Stores episodic and semantic memory for error analysis"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="error_memories",
        null=False,
        blank=False,
    )

    # Memory type
    memory_type = models.CharField(
        max_length=20, null=False, blank=False
    )  # episodic, semantic

    # Memory content
    memory_key = models.CharField(
        max_length=200, null=False, blank=False
    )  # e.g., "tool_usage_pattern", "similar_traces"
    memory_data = models.JSONField(default=dict, blank=True)

    # Memory metadata
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    memory_window_days = models.IntegerField(default=30)
    is_active = models.BooleanField(default=True)

    # Usage tracking
    access_count = models.IntegerField(default=0)
    last_accessed = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_error_memory"
        ordering = ["-last_updated"]
        indexes = [
            models.Index(fields=["project", "memory_type"]),
            models.Index(fields=["memory_key"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["last_updated"]),
        ]


class ErrorClusterTraces(BaseModel):
    """Stores the traces that are clustered together, with provenance."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="error_cluster_traces",
        null=True,
        blank=True,
    )

    span = models.ForeignKey(
        ObservationSpan,
        on_delete=models.CASCADE,
        related_name="error_cluster_spans",
        null=True,
        blank=True,
    )
    cluster = models.ForeignKey(
        TraceErrorGroup,
        on_delete=models.CASCADE,
        related_name="clusters",
        null=False,
        blank=False,
    )

    # Provenance — which finding caused this trace to join the cluster
    scan_issue = models.ForeignKey(
        "tracer.TraceScanIssue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cluster_memberships",
        help_text="Scanner finding that caused membership",
    )
    eval_logger = models.ForeignKey(
        EvalLogger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cluster_memberships",
        help_text="Eval result that caused membership",
    )

    class Meta:
        db_table = "tracer_error_cluster_traces"
        unique_together = [["cluster", "trace", "span"]]
        indexes = [
            models.Index(fields=["cluster", "trace"]),
            models.Index(fields=["cluster"]),
            models.Index(fields=["trace"]),
            models.Index(fields=["span"]),
            models.Index(fields=["scan_issue"]),
            models.Index(fields=["eval_logger"]),
            models.Index(
                fields=["cluster", "-created_at"],
                name="tracer_ect_cluster_created_idx",
            ),
        ]

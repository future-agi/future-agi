"""
Type definitions and dataclasses for model_hub
"""

from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, Field


@dataclass
class ConversionResult:
    """Result of converting a single cell during datatype conversion"""

    cell_id: str
    success: bool
    new_value: Optional[str]
    status: str
    value_infos: dict
    error_message: Optional[str] = None


# =============================================================================
# Eval List Types (Phase 1)
# =============================================================================


class EvalListFilters(BaseModel):
    """Filters for the eval template list endpoint."""

    eval_type: list[Literal["llm", "code", "agent"]] | None = None
    output_type: list[Literal["pass_fail", "percentage", "deterministic"]] | None = None
    template_type: list[Literal["single", "composite"]] | None = None
    tags: list[str] | None = None
    created_by: list[str] | None = None


class EvalListRequest(BaseModel):
    """Request schema for POST /model-hub/eval-templates/list/"""

    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=25, ge=1, le=100)
    search: str | None = None
    owner_filter: Literal["all", "user", "system"] = "all"
    filters: EvalListFilters | None = None
    sort_by: Literal["name", "updated_at", "created_at"] = "updated_at"
    sort_order: Literal["asc", "desc"] = "desc"


class ThirtyDayDataPoint(BaseModel):
    """Single data point for 30-day sparkline charts."""

    timestamp: str
    value: float


class EvalListItem(BaseModel):
    """Single item in the eval template list response."""

    id: str
    name: str
    template_type: Literal["single", "composite"]
    eval_type: Literal["llm", "code", "agent"]
    output_type: Literal["pass_fail", "percentage", "deterministic"]
    owner: Literal["system", "user"]
    created_by_name: str
    version_count: int
    current_version: str
    last_updated: str
    thirty_day_chart: list[ThirtyDayDataPoint]
    thirty_day_error_rate: list[ThirtyDayDataPoint]
    thirty_day_run_count: int
    tags: list[str]


class EvalListResponse(BaseModel):
    """Response schema for POST /model-hub/eval-templates/list/"""

    items: list[EvalListItem]
    total: int
    page: int
    page_size: int


class BulkDeleteRequest(BaseModel):
    """Request schema for POST /model-hub/eval-templates/bulk-delete/"""

    template_ids: list[str] = Field(min_length=1, max_length=100)


class BulkDeleteResponse(BaseModel):
    """Response schema for POST /model-hub/eval-templates/bulk-delete/"""

    deleted_count: int


# =============================================================================
# Eval Create Types (Phase 3)
# =============================================================================


class EvalCreateRequest(BaseModel):
    """Request schema for POST /model-hub/eval-templates/create-v2/"""

    class Config:
        extra = "forbid"

    name: str = Field(min_length=0, max_length=255, default="")
    # Accept camelCase `isDraft` as an alias. The frontend's response
    # interceptor installs enumerable camelCase twins on every response
    # object; if a caller spreads response-derived state into a request
    # body without also setting `is_draft`, the twin `isDraft` is what
    # arrives here. Treating the two names as equivalent keeps the draft
    # intent intact and stops the "Instructions are required" 400 from
    # firing at mount time (TH-4076).
    is_draft: bool = Field(
        default=False,
        validation_alias=AliasChoices("is_draft", "isDraft"),
    )
    eval_type: Literal["llm", "code", "agent"] = "llm"
    instructions: str = Field(default="", max_length=100000)
    model: str = Field(default="turing_large", max_length=255)
    output_type: Literal["pass_fail", "percentage", "deterministic"] = "pass_fail"
    pass_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    choice_scores: dict[str, float] | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    check_internet: bool = False
    # Code eval fields
    code: str | None = Field(default=None, max_length=100000)
    code_language: Literal["python", "javascript"] | None = None
    # LLM-as-a-judge fields
    messages: list[dict] | None = (
        None  # [{role: "system"|"user"|"assistant", content: "..."}]
    )
    few_shot_examples: list[dict] | None = (
        None  # [{input: "...", output: "...", score: "..."}]
    )
    # Agent eval fields
    mode: Literal["auto", "agent", "quick"] | None = None
    tools: dict | None = None  # {internet: bool, connectors: [str]}
    knowledge_bases: list[str] | None = None
    data_injection: dict | None = (
        None  # {variables_only, dataset_row, trace_context, ...}
    )
    summary: dict | None = (
        None  # {type: short|long|concise|custom, custom: str}
    )
    # Error Localization — mirrors EvalUpdateRequest. Without this here the
    # FE's create payload (which always includes the toggle value) is
    # rejected by `extra="forbid"`.
    error_localizer_enabled: bool = False
    # Template format — determines how variables are extracted
    template_format: Literal["mustache", "jinja"] = "mustache"


class EvalCreateResponse(BaseModel):
    """Response schema for POST /model-hub/eval-templates/create-v2/"""

    id: str
    name: str
    version: str


# =============================================================================
# Eval Detail Types (Phase 4)
# =============================================================================


class EvalDetailResponse(BaseModel):
    """Response schema for GET /model-hub/eval-templates/{id}/detail/"""

    id: str
    name: str
    description: str | None = None
    template_type: Literal["single", "composite"] = "single"
    eval_type: Literal["llm", "code", "agent"] = "llm"
    instructions: str | None = None
    model: str | None = None
    output_type: Literal["pass_fail", "percentage", "deterministic"] = "percentage"
    pass_threshold: float = 0.5
    choice_scores: dict[str, float] | None = None
    choices: list | dict | None = None
    multi_choice: bool = False
    # Code eval fields (exposed at top level for convenience)
    code: str | None = None
    code_language: str | None = None
    required_keys: list[str] = Field(default_factory=list)
    owner: Literal["system", "user"] = "user"
    created_by_name: str = ""
    version_count: int = 1
    current_version: str = "V1"
    tags: list[str] = Field(default_factory=list)
    check_internet: bool = False
    error_localizer_enabled: bool = False
    template_format: Literal["mustache", "jinja"] = "mustache"
    # Composite aggregation config (only meaningful when template_type == "composite")
    aggregation_enabled: bool = True
    aggregation_function: str = "weighted_avg"
    composite_child_axis: str = ""
    config: dict | None = None
    created_at: str = ""
    updated_at: str = ""


class EvalUpdateRequest(BaseModel):
    """Request schema for PUT /model-hub/eval-templates/{id}/update/"""

    class Config:
        extra = "forbid"

    name: str | None = None
    eval_type: Literal["llm", "code", "agent"] | None = None
    instructions: str | None = None
    model: str | None = None
    output_type: Literal["pass_fail", "percentage", "deterministic"] | None = None
    pass_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    choice_scores: dict[str, float] | None = None
    multi_choice: bool | None = None
    description: str | None = None
    tags: list[str] | None = None
    check_internet: bool | None = None
    # Code eval fields
    code: str | None = None
    code_language: Literal["python", "javascript"] | None = None
    # LLM-as-a-judge fields
    messages: list[dict] | None = None
    few_shot_examples: list[dict] | None = None
    # Agent eval fields
    mode: Literal["auto", "agent", "quick"] | None = None
    tools: dict | None = None
    knowledge_bases: list[str] | None = None
    data_injection: dict | None = None
    summary: dict | None = None
    # Error Localization (Phase 19)
    error_localizer_enabled: bool | None = None
    # Draft → publish
    publish: bool | None = None
    # Template format
    template_format: Literal["mustache", "jinja"] | None = None


class EvalUpdateResponse(BaseModel):
    """Response schema for PUT /model-hub/eval-templates/{id}/update/"""

    id: str
    name: str
    updated: bool = True


# =============================================================================
# Eval Versioning Types (Phase 5)
# =============================================================================


class EvalVersionItem(BaseModel):
    """Single version in the version list."""

    id: str
    version_number: int
    is_default: bool
    criteria: str = ""
    model: str = ""
    config_snapshot: dict = Field(default_factory=dict)
    created_by_name: str = ""
    created_at: str = ""


class EvalVersionListResponse(BaseModel):
    """Response for GET /model-hub/eval-templates/{id}/versions/"""

    template_id: str
    versions: list[EvalVersionItem]
    total: int


class CreateVersionRequest(BaseModel):
    """Request for POST /model-hub/eval-templates/{id}/versions/create/"""

    criteria: str | None = None
    model: str | None = None
    config_snapshot: dict | None = None


class CreateVersionResponse(BaseModel):
    """Response for POST /model-hub/eval-templates/{id}/versions/create/"""

    id: str
    version_number: int
    is_default: bool


# =============================================================================
# Composite Eval Types (Phase 7)
# =============================================================================


AGGREGATION_FUNCTIONS = ["weighted_avg", "avg", "min", "max", "pass_rate"]
COMPOSITE_CHILD_AXES = ["pass_fail", "percentage", "choices", "code"]


class CompositeChildItem(BaseModel):
    """A child eval within a composite."""

    child_id: str
    child_name: str
    order: int
    eval_type: str = "llm"
    pinned_version_id: str | None = None
    pinned_version_number: int | None = None
    weight: float = 1.0
    # Populated from the child template's config so the EvalPicker can
    # show a single combined mapping panel for composites. Empty for
    # children with no declared required variables.
    required_keys: list[str] = Field(default_factory=list)


class CompositeCreateRequest(BaseModel):
    """Request for POST /model-hub/eval-templates/create-composite/"""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    child_template_ids: list[str] = Field(min_length=1, max_length=50)
    aggregation_enabled: bool = True
    aggregation_function: str = "weighted_avg"
    child_weights: dict[str, float] | None = None
    # Empty string means legacy / unset: no homogeneity enforcement.
    # Frontend always sends a real axis.
    composite_child_axis: str = ""


class CompositeCreateResponse(BaseModel):
    """Response for POST /model-hub/eval-templates/create-composite/"""

    id: str
    name: str
    template_type: str = "composite"
    aggregation_enabled: bool = True
    aggregation_function: str = "weighted_avg"
    composite_child_axis: str = ""
    children: list[CompositeChildItem]


class CompositeDetailResponse(BaseModel):
    """Response for GET /model-hub/eval-templates/{id}/composite/"""

    id: str
    name: str
    description: str | None = None
    template_type: str = "composite"
    aggregation_enabled: bool = True
    aggregation_function: str = "weighted_avg"
    composite_child_axis: str = ""
    children: list[CompositeChildItem]
    tags: list[str]
    created_at: str = ""
    updated_at: str = ""
    version_number: int | None = None


class CompositeUpdateRequest(BaseModel):
    """Request for PATCH /model-hub/eval-templates/{id}/composite/

    All fields are optional. Only supplied fields are updated. Passing
    `child_template_ids` replaces the child list entirely.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] | None = None
    aggregation_enabled: bool | None = None
    aggregation_function: str | None = None
    child_template_ids: list[str] | None = Field(
        default=None, min_length=1, max_length=50
    )
    child_weights: dict[str, float] | None = None
    composite_child_axis: str | None = None


class CompositeExecuteRequest(BaseModel):
    """Request for POST /model-hub/eval-templates/{id}/composite/execute/"""

    mapping: dict[str, Any]
    model: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    error_localizer: bool = False
    input_data_types: dict[str, str] = Field(default_factory=dict)
    span_context: dict | None = None
    trace_context: dict | None = None
    session_context: dict | None = None
    call_context: dict | None = None
    row_context: dict | None = None


class CompositeAdhocExecuteRequest(BaseModel):
    """Request for POST /model-hub/eval-templates/composite/execute-adhoc/

    Run a composite eval without persisting it. Used by the eval create
    page so users can test a composite configuration before saving.
    """

    child_template_ids: list[str] = Field(min_length=1, max_length=50)
    aggregation_enabled: bool = True
    aggregation_function: str = "weighted_avg"
    composite_child_axis: str = ""
    child_weights: dict[str, float] | None = None
    pass_threshold: float = 0.5

    mapping: dict[str, Any]
    model: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    error_localizer: bool = False
    input_data_types: dict[str, str] = Field(default_factory=dict)
    span_context: dict | None = None
    trace_context: dict | None = None
    session_context: dict | None = None
    call_context: dict | None = None
    row_context: dict | None = None


class CompositeChildResult(BaseModel):
    """Result of executing a single child eval within a composite."""

    child_id: str
    child_name: str
    order: int
    score: float | None = None
    output: Any = None
    reason: str | None = None
    output_type: str | None = None
    status: str = "completed"
    error: str | None = None
    log_id: str | None = None
    weight: float = 1.0
    error_localizer_result: dict | None = None


class CompositeExecuteResponse(BaseModel):
    """Response for POST /model-hub/eval-templates/{id}/composite/execute/"""

    composite_id: str
    composite_name: str
    aggregation_enabled: bool
    aggregation_function: str | None = None
    aggregate_score: float | None = None
    aggregate_pass: bool | None = None
    children: list[CompositeChildResult]
    summary: str | None = None
    error_localizer_results: dict | None = None
    total_children: int
    completed_children: int
    failed_children: int
    evaluation_id: str | None = None


# =============================================================================
# Ground Truth Types (Phase 9)
# =============================================================================


class GroundTruthUploadRequest(BaseModel):
    """Request for POST /model-hub/eval-templates/{id}/ground-truth/upload/ (JSON body)"""

    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    file_name: str = ""
    columns: list[str]
    data: list[dict]
    variable_mapping: dict[str, str] | None = None
    role_mapping: dict[str, str] | None = None


class GroundTruthItem(BaseModel):
    """Single ground truth dataset."""

    id: str
    name: str
    description: str = ""
    file_name: str = ""
    columns: list[str]
    row_count: int
    variable_mapping: dict[str, str] | None = None
    role_mapping: dict[str, str] | None = None
    embedding_status: str = "pending"
    embedded_row_count: int = 0
    storage_type: str = "db"
    created_at: str = ""


class GroundTruthListResponse(BaseModel):
    """Response for GET /model-hub/eval-templates/{id}/ground-truth/"""

    template_id: str
    items: list[GroundTruthItem]
    total: int


class GroundTruthUploadResponse(BaseModel):
    """Response for POST /model-hub/eval-templates/{id}/ground-truth/upload/"""

    id: str
    name: str
    row_count: int
    columns: list[str]
    embedding_status: str = "pending"


class VariableMappingRequest(BaseModel):
    """Request for PUT /model-hub/ground-truth/{id}/mapping/"""

    variable_mapping: dict[str, str]


class RoleMappingRequest(BaseModel):
    """Request for PUT /model-hub/ground-truth/{id}/role-mapping/"""

    role_mapping: dict[str, str]


class GroundTruthDataResponse(BaseModel):
    """Response for GET /model-hub/ground-truth/{id}/data/"""

    id: str
    page: int
    page_size: int
    total_rows: int
    total_pages: int
    columns: list[str]
    rows: list[dict]


class GroundTruthStatusResponse(BaseModel):
    """Response for GET /model-hub/ground-truth/{id}/status/"""

    id: str
    embedding_status: str
    embedded_row_count: int
    total_rows: int
    progress_percent: float


class GroundTruthConfigRequest(BaseModel):
    """Request for PUT /model-hub/eval-templates/{id}/ground-truth-config/"""

    enabled: bool = True
    ground_truth_id: str | None = None
    mode: str = "auto"  # auto | manual | disabled
    max_examples: int = Field(default=3, ge=1, le=10)
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    injection_format: str = "structured"  # structured | conversational | xml


class GroundTruthSearchRequest(BaseModel):
    """Request for POST /model-hub/ground-truth/{id}/search/"""

    query: str = Field(min_length=1)
    max_results: int = Field(default=3, ge=1, le=20)


# =============================================================================
# Usage & Feedback Types (Phase 10)
# =============================================================================


class EvalUsageStats(BaseModel):
    """Usage statistics for an eval template."""

    template_id: str
    total_runs: int = 0
    runs_last_30_days: int = 0
    success_count: int = 0
    error_count: int = 0
    pass_rate: float = 0.0
    avg_runtime_ms: float = 0.0


class FeedbackItem(BaseModel):
    """Single feedback entry."""

    id: str
    value: str
    explanation: str | None = None
    source: str = ""
    created_at: str = ""


class EvalFeedbackResponse(BaseModel):
    """Response for GET /model-hub/eval-templates/{id}/feedback/"""

    template_id: str
    items: list[FeedbackItem]
    total: int


# =============================================================================
# Trace/Session Eval Types (Phase 11)
# =============================================================================


class TraceEvalRequest(BaseModel):
    """Request for POST /model-hub/eval-templates/{id}/run-on-trace/"""

    trace_id: str
    model: str = "turing_large"
    pass_context: bool = False


class TraceEvalResponse(BaseModel):
    """Response for POST /model-hub/eval-templates/{id}/run-on-trace/"""

    template_id: str
    trace_id: str
    score: float | None = None
    passed: bool | None = None
    reason: str | None = None
    status: str = "completed"


# =============================================================================
# Version Comparison Types (Phase 12)
# =============================================================================


class VersionDiff(BaseModel):
    """Diff between two versions of an eval template."""

    field: str
    version_a_value: str | None = None
    version_b_value: str | None = None
    changed: bool = False


class VersionCompareResponse(BaseModel):
    """Response for GET /model-hub/eval-templates/{id}/versions/compare/"""

    template_id: str
    version_a: int
    version_b: int
    diffs: list[VersionDiff]

from dataclasses import dataclass


@dataclass
class ImagineAnalysisInput:
    """Input for a single widget analysis workflow."""

    analysis_id: str  # ImagineAnalysis PK
    trace_id: str  # trace to analyze
    org_id: str  # organization for DB access
    prompt: str  # analysis prompt from the widget
    # Optional for decoding in-flight workflows; missing scope fails closed.
    project_id: str | None = None


@dataclass
class ImagineAnalysisOutput:
    """Output from analysis workflow."""

    content: str  # markdown analysis result


@dataclass
class FetchTraceInput:
    trace_id: str
    org_id: str
    project_id: str | None = None


@dataclass
class RunAnalysisInput:
    prompt: str
    trace_context: str  # formatted trace data for LLM
    org_id: str  # organization for capability gating
    # Optional for decoding queued activities; missing scope fails closed.
    project_id: str | None = None


@dataclass
class SaveResultInput:
    analysis_id: str
    content: str
    status: str = "completed"
    error: str | None = None
    # Older scheduled saves lack these fields and may only record failure.
    project_id: str | None = None
    org_id: str | None = None

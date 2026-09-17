from dataclasses import dataclass
from typing import Optional


@dataclass
class ImagineAnalysisInput:
    """Input for a single widget analysis workflow."""

    analysis_id: str  # ImagineAnalysis PK
    trace_id: str  # trace to analyze
    org_id: str  # organization for DB access
    prompt: str  # analysis prompt from the widget


@dataclass
class ImagineAnalysisOutput:
    """Output from analysis workflow."""

    content: str  # markdown analysis result


@dataclass
class FetchTraceInput:
    trace_id: str
    org_id: str


@dataclass
class RunAnalysisInput:
    prompt: str
    trace_context: str  # formatted trace data for LLM
    org_id: str  # organization for capability gating


@dataclass
class SaveResultInput:
    analysis_id: str
    content: str
    status: str = "completed"
    error: Optional[str] = None

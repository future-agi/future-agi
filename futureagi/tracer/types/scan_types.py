"""
Typed dataclasses for the trace scanner + clustering pipeline.

Single source of truth — queries, utils, and tasks all import from here.
"""

from dataclasses import dataclass, field
from typing import Dict, List

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Scanner types
# ---------------------------------------------------------------------------


@dataclass
class ScanConfig:
    """Resolved scan configuration for a project."""

    sampling_rate: float = 1.0
    scan_version: str = "v7.2"
    enabled: bool = True


@dataclass
class SpanData:
    """Span formatted for the scanner (matches TRAIL dataset structure)."""

    span_id: str
    span_name: str
    duration: str
    status_code: str
    span_attributes: Dict[str, str] = field(default_factory=dict)
    child_spans: list = field(default_factory=list)


@dataclass
class TraceData:
    """Trace with nested span tree, ready for scanner input."""

    trace_id: str
    spans: List[SpanData] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict format the scanner expects."""

        def _span_to_dict(span: SpanData) -> dict:
            return {
                "span_id": span.span_id,
                "span_name": span.span_name,
                "duration": span.duration,
                "status_code": span.status_code,
                "span_attributes": span.span_attributes,
                "child_spans": [_span_to_dict(c) for c in span.child_spans],
            }

        return {
            "trace_id": self.trace_id,
            "spans": [_span_to_dict(s) for s in self.spans],
        }


# ---------------------------------------------------------------------------
# Clustering types
# ---------------------------------------------------------------------------


@dataclass
class ClusterableIssue:
    """Issue with context needed for clustering."""

    issue_id: str
    trace_id: str
    project_id: str
    category: str
    group: str
    fix_layer: str
    brief: str
    confidence: str
    key_moments_text: List[str] = field(default_factory=list)

    @property
    def embedding_text(self) -> str:
        """Embed only the issue brief."""
        return self.brief


@dataclass
class ClusteringSummary:
    """Result of a clustering run."""

    clustered: int = 0
    new_clusters: int = 0
    assigned: int = 0


# ---------------------------------------------------------------------------
# Success trace matching types
# ---------------------------------------------------------------------------


@dataclass
class TraceInputData:
    """Root span input for a trace, ready for embedding."""

    trace_id: str
    project_id: str
    input_text: str
    has_issues: bool = False

    @property
    def kevinified_text(self) -> str:
        """Lazy import kevinify to avoid circular deps."""
        try:
            from ee.agenthub.trace_scanner.compress import kevinify
        except ImportError:
            if settings.DEBUG:
                logger.warning("Could not import ee.agenthub.trace_scanner.compress", exc_info=True)
            return None

        return kevinify(self.input_text)


@dataclass
class SuccessTraceMatch:
    """Result of KNN success trace matching for a cluster."""

    cluster_id: str
    success_trace_id: str
    distance: float

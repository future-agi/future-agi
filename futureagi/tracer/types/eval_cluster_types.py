"""
Typed dataclasses for the eval clustering pipeline.

Mirrors scan_types.py — single source of truth for eval clustering.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ClusterableEvalResult:
    """Failing eval result with context needed for clustering."""

    eval_logger_id: str
    project_id: str
    eval_name: str  # CustomEvalConfig.name — partition key (with target_type)
    eval_config_id: str  # FK for TraceErrorGroup.eval_config
    explanation: str  # eval_explanation text — embedding input
    # The eval target. span/trace results carry trace_id (session_id None);
    # session results carry session_id (trace_id None). The junction row and
    # the centroid family both branch on this — the three targets never share
    # a cluster.
    target_type: str = "span"
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    score: Optional[float] = None  # output_float if available
    # Canonical failure phrase distilled from the explanation by a cheap
    # LLM (trace-specific noise stripped). None when distillation is
    # unavailable (OSS) or failed — embedding falls back to the raw text.
    distilled: Optional[str] = None

    @property
    def embedding_text(self) -> str:
        return self.distilled or self.explanation


@dataclass
class EvalClusteringSummary:
    """Result of an eval clustering run."""

    clustered: int = 0
    new_clusters: int = 0
    assigned: int = 0


@dataclass
class EvalClusterMeta:
    """Cheap-LLM-derived metadata for an eval cluster. Any field may be
    None — the caller falls back per field (title -> first-sentence,
    severity -> default priority, fix_layer -> unset)."""

    title: Optional[str] = None
    fix_layer: Optional[str] = None  # Tools|Prompt|Orchestration|Guardrails
    severity: Optional[str] = None  # critical|high|medium|low

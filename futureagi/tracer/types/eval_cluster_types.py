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
    trace_id: str
    project_id: str
    eval_name: str  # CustomEvalConfig.name — partition key
    eval_config_id: str  # FK for TraceErrorGroup.eval_config
    explanation: str  # eval_explanation text — embedding input
    score: Optional[float] = None  # output_float if available

    @property
    def embedding_text(self) -> str:
        return self.explanation


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

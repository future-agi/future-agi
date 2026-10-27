"""Cluster RCA agent — investigates a TraceErrorGroup, produces synthesis + findings.

Mirrors the Judge agent shape (agentic tool-loop) but operates at cluster scope
instead of single-trace scope.
"""

from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent
from ee.agenthub.cluster_rca.types import (
    ClusterAnalysisResult,
    ClusterFinding,
    ClusterSynthesis,
)

__all__ = [
    "ClusterAnalysisAgent",
    "ClusterAnalysisResult",
    "ClusterFinding",
    "ClusterSynthesis",
]

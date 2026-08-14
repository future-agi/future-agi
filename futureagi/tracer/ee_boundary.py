"""Single EE boundary for tracer-layer scanner utilities.

OSS call sites import from HERE, never from ee.* directly.
Each function is a no-op / passthrough when ee is absent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracer.types.eval_cluster_types import ClusterableEvalResult, EvalClusterMeta
    from tracer.types.scan_types import ClusterableIssue

logger = logging.getLogger(__name__)

_ee_available: bool
try:
    from ee.agenthub.trace_scanner.eval_cluster_title import (
        distill_failure_phrases as _distill_failure_phrases,
        generate_eval_cluster_meta as _generate_eval_cluster_meta,
        generate_scan_cluster_severity as _generate_scan_cluster_severity,
        generate_scan_cluster_title as _generate_scan_cluster_title,
    )
    from ee.agenthub.trace_scanner.compress import (
        attribute_key_moments as _attribute_key_moments,
    )

    _ee_available = True
except ImportError:
    _ee_available = False


def generate_scan_cluster_severity(category: str, brief: str) -> str | None:
    """Classify user-impact severity for a new scanner cluster's seed issue.
    Returns None on OSS or LLM failure (caller defaults to medium)."""
    if not _ee_available:
        return None
    try:
        return _generate_scan_cluster_severity(category, brief)
    except Exception:
        logger.warning("scan_cluster_severity_llm_failed", exc_info=True)
        return None


def generate_eval_cluster_meta(
    eval_name: str, reasoning: str,
) -> EvalClusterMeta | None:
    """LLM-generated title + fix_layer + severity for an eval cluster.
    Returns None on OSS or LLM failure (caller falls back to deterministic title)."""
    if not _ee_available:
        return None
    try:
        return _generate_eval_cluster_meta(eval_name, reasoning)
    except Exception:
        logger.warning("eval_cluster_meta_llm_failed", exc_info=True)
        return None


def generate_scan_cluster_title(briefs: list) -> str | None:
    """One entity-free title describing what a scanner cluster's members share.

    Returns None on OSS, on LLM failure, or when the model declines / produces a
    title that still names a ticker or amount — the caller then keeps its
    deterministic medoid title.
    """
    if not _ee_available:
        return None
    try:
        return _generate_scan_cluster_title(briefs)
    except Exception:
        logger.warning("scan_cluster_title_llm_failed", exc_info=True)
        return None


def distill_eval_failure_phrases(
    results: list[ClusterableEvalResult],
) -> list[ClusterableEvalResult]:
    """Distill verbose eval explanations to canonical failure phrases.
    Mutates each result's .distilled in place. No-op on OSS."""
    if not _ee_available:
        return results
    try:
        phrases = _distill_failure_phrases(
            [(r.eval_name, r.explanation) for r in results]
        )
        for result, phrase in zip(results, phrases, strict=True):
            result.distilled = phrase
    except Exception:
        logger.warning("distill_failure_phrases_failed", exc_info=True)
    return results


def distill_scan_briefs(issues: list[ClusterableIssue]) -> list[ClusterableIssue]:
    """Distill scanner issue briefs to canonical failure phrases.
    Mutates each issue's .distilled in place. No-op on OSS."""
    if not _ee_available:
        return issues
    try:
        phrases = _distill_failure_phrases([(i.category, i.brief) for i in issues])
        for issue, phrase in zip(issues, phrases, strict=True):
            issue.distilled = phrase
    except Exception:
        logger.warning("distill_scan_briefs_failed", exc_info=True)
    return issues


def attribute_key_moments(
    key_moments: list[dict], trace_id: str, project_id: str
) -> list[dict]:
    """Reconstruct span attribution for old scans whose stored key_moments
    predate role attribution. No-op on OSS or when spans are unavailable.

    ``project_id`` scopes the span read to the trace's tenant so the ClickHouse
    primary-key prefix prunes the scan."""
    if not _ee_available:
        return key_moments
    try:
        from tracer.queries.trace_scanner import fetch_trace_data

        traces = fetch_trace_data([trace_id], project_id)
        if not traces:
            return key_moments
        trace_dict = traces[0].to_dict()
        quotes = [
            (km.get("kevinified") or km.get("verbatim") or "")
            for km in key_moments
        ]
        attribution = _attribute_key_moments(quotes, trace_dict)
        return [
            {**km, **attr} if not km.get("role") else km
            for km, attr in zip(key_moments, attribution, strict=False)
        ]
    except Exception:
        logger.warning("attribute_key_moments_failed", exc_info=True)
        return key_moments

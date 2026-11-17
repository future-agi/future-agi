"""Attribute-only views of sealed model evidence for background consumers.

Model fields have no get/default API. Operational metadata stays separate from
model assessments; no missing assessment is supplied by a persistence default.
"""

from dataclasses import dataclass
from types import MappingProxyType

from ee.agenthub.traceerroragent.background_payloads import (
    ValidatedPayload,
    refuse,
    require_payload,
)


@dataclass(frozen=True)
class Evidence:
    _fields: object

    def __getattr__(self, field):
        return self._fields[field]


def _view(value):
    if type(value) is dict:
        return Evidence(MappingProxyType({k: _view(v) for k, v in value.items()}))
    if type(value) is list:
        return tuple(_view(v) for v in value)
    return value


def evidence(payload, owner, kind):
    return _view(require_payload(payload, owner, kind))


def save_publication(trace_id, project, payload, analyses, details):
    from ee.agenthub.traceerroragent.background_payload_reports import (
        require_publication,
    )

    if not isinstance(payload, ValidatedPayload):
        refuse(None)
    data = require_publication(payload)
    report = evidence(payload, payload._witness, "publication")
    # Host metadata is not model evidence. V2 explicitly has no grouping stage.
    memory = data.get("memory_context", {})
    if data.get("grouped_errors", []):
        refuse(payload._witness)
    summary, scores, errors = report.summary, report.scores, report.errors
    analysis = analyses.drafts.create(
        trace_id=trace_id,
        project=project,
        agent_version="1.0",
        memory_enhanced=memory.get("episodic_memory_used", False),
        overall_score=summary.overall_score,
        total_errors=summary.error_count,
        high_impact_errors=sum(e.impact == "HIGH" for e in errors),
        medium_impact_errors=sum(e.impact == "MEDIUM" for e in errors),
        low_impact_errors=sum(e.impact == "LOW" for e in errors),
        recommended_priority=summary.recommended_priority,
        factual_grounding_score=scores.factual_grounding.score,
        factual_grounding_reason=scores.factual_grounding.reason,
        privacy_and_safety_score=scores.privacy_and_safety.score,
        privacy_and_safety_reason=scores.privacy_and_safety.reason,
        instruction_adherence_score=scores.instruction_adherence.score,
        instruction_adherence_reason=scores.instruction_adherence.reason,
        optimal_plan_execution_score=scores.optimal_plan_execution.score,
        optimal_plan_execution_reason=scores.optimal_plan_execution.reason,
        insights=summary.insights,
        memory_context=memory,
        grouped_errors_count=0,
    )
    for error in errors:
        details.drafts.create(
            analysis=analysis,
            error_id=error.error_id,
            cluster_id=error.cluster_id,
            category=error.category,
            impact=error.impact,
            urgency_to_fix=error.urgency_to_fix,
            location_spans=list(error.location_spans),
            evidence_snippets=list(error.evidence_snippets),
            description=error.description,
            root_causes=list(error.root_causes),
            recommendation=error.recommendation,
            immediate_fix=error.immediate_fix,
            trace_impact=error.trace_impact,
            # These legacy operational columns are not produced by V2 Judge.
            trace_assessment=None,
            llm_analysis=None,
            memory_enhanced=False,
        )
    return analysis

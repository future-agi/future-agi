"""Explicit schemas for every generation-owned model report and Judge tool."""

TEXT = {"type": "string"}
STRINGS = {"type": "array", "items": TEXT}
SCORE = {"type": "number", "minimum": 1, "maximum": 5}
CONFIDENCE = {"type": "number", "minimum": 0.7, "maximum": 1}
DIMENSIONS = (
    "factual_grounding",
    "privacy_and_safety",
    "instruction_adherence",
    "optimal_plan_execution",
)


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def array(items, *, empty=False):
    return {"type": "array", "items": items, "minItems": 0 if empty else 1}


PRIORITY = {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]}
FLOW = obj(
    {"name": TEXT, "spans": STRINGS, "summary": TEXT, "flags": array(TEXT, empty=True)}
)
CHAUFFEUR = {
    "anyOf": [
        obj({"sub_flows": array(FLOW), "trace_overview": TEXT}),
        obj({"span_groups": array(obj({"signal": TEXT, "spans": STRINGS}))}),
    ]
}
SCORES = obj({name: obj({"score": SCORE, "reason": TEXT}) for name in DIMENSIONS})
SUMMARY = obj(
    {
        "overall_score": SCORE,
        "error_count": {"type": "integer", "minimum": 0},
        "insights": TEXT,
        "recommended_priority": PRIORITY,
    }
)
FINDING_FIELDS = {
    "category": TEXT,
    "location_spans": STRINGS,
    "evidence_snippets": STRINGS,
    "description": TEXT,
    "impact": PRIORITY,
    "root_causes": STRINGS,
    "recommendation": TEXT,
    "confidence": CONFIDENCE,
    "urgency_to_fix": {
        "type": "string",
        "enum": ["IMMEDIATE", "HIGH", "MEDIUM", "LOW"],
    },
    "immediate_fix": TEXT,
    "trace_impact": TEXT,
}
FINDING_REQUIRED = list(FINDING_FIELDS)
FINDING = obj(FINDING_FIELDS, FINDING_REQUIRED)
# Persistence-only fields are supplied by our code, never default-filled model
# evidence. All consumed model assessments must be present and nonempty.
ERROR = obj(
    {**FINDING_FIELDS, "error_id": TEXT, "cluster_id": TEXT},
    [*FINDING_REQUIRED, "error_id", "cluster_id"],
)
JUDGE_REPORT = obj(
    {"errors": array(ERROR, empty=True), "scores": SCORES, "summary": SUMMARY}
)
JUDGE_ARGUMENTS = {
    "read_span": obj({"span_id": TEXT}),
    "read_span_exact": obj({"span_id": TEXT}),
    "get_children": obj({"span_id": TEXT}),
    "get_spans_by_type": obj({"observation_type": TEXT}),
    "search_spans": obj({"keyword": TEXT}),
    "submit_finding": FINDING,
    "submit_scores": SCORES,
    "submit_summary": SUMMARY,
}

"""Load actual payload modules without Django package initializers.

Old downstream unit probes model an already validated dispatch boundary. Their
synthetic UUIDs are labels, not UUID-format tests; the original-JSON worker suite
separately executes the unchanged real input schemas with actual UUID strings.
"""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).parents[2]


def modules(existing):
    result = dict(existing)
    transport_name = "tracer.services.background_transport"
    if transport_name not in result:
        transport = ModuleType(transport_name)
        exec(
            compile(
                (ROOT / "tracer/services/background_transport.py").read_text(),
                transport_name,
                "exec",
            ),
            vars(transport),
        )
        result[transport_name] = transport
    for name in (
        "background_payloads",
        "background_payload_schemas",
        "background_payload_reports",
        "background_payload_tools",
        "background_payload_judge",
        "background_payload_wire",
        "background_payload_consumers",
        "background_gateway",
        "background_wire",
    ):
        fullname = "ee.agenthub.traceerroragent." + name
        mod = ModuleType(fullname)
        result[fullname] = mod
        with patch.dict(sys.modules, result):
            exec(
                compile(
                    (ROOT / (fullname.replace(".", "/") + ".py")).read_text(),
                    fullname,
                    "exec",
                ),
                vars(mod),
            )
    return result


def parameters(params, guard, name):
    from ee.agenthub.traceerroragent.background_payload_tools import ToolParameters
    from ee.agenthub.traceerroragent.background_payloads import validate_payload

    if isinstance(params, ToolParameters):
        return params
    # These downstream probes deliberately test invalid semantic fields too;
    # only structural type evidence is supplied by this modeled boundary.
    data = vars(params).copy()

    def schema(value):
        if isinstance(value, list):
            return {
                "type": "array",
                "items": {"type": "string", "minLength": 0},
                "minItems": 0,
            }
        return {
            "type": {
                str: "string",
                bool: "boolean",
                int: "integer",
                float: "number",
                type(None): "null",
            }[type(value)],
            "minLength": 0,
        }

    spec = {
        "type": "object",
        "properties": {k: schema(v) for k, v in data.items()},
        "required": list(data),
    }
    return ToolParameters(validate_payload(data, spec, guard, "tool:" + name))


def healthy_report():
    return {
        "errors": [
            {
                "error_id": "E1",
                "cluster_id": "C1",
                "category": "Accuracy > Reasoning",
                "description": "published finding",
                "recommendation": "fix",
                "immediate_fix": "retry",
                "urgency_to_fix": "HIGH",
                "trace_impact": "The answer is incorrect.",
                "root_causes": ["cause"],
                "location_spans": ["span"],
                "evidence_snippets": ["answer"],
                "impact": "HIGH",
                "confidence": 0.9,
            }
        ],
        "scores": {
            key: {"score": 4, "reason": "evidence"}
            for key in (
                "factual_grounding",
                "privacy_and_safety",
                "instruction_adherence",
                "optimal_plan_execution",
            )
        },
        "summary": {
            "overall_score": 4,
            "error_count": 1,
            "insights": "complete",
            "recommended_priority": "HIGH",
        },
    }


def sealed_report(identity):
    from ee.agenthub.traceerroragent.background_payload_reports import (
        judge_report,
        publication_result,
    )
    from tracer.services.analysis_result_completion import CompletionWitness

    witness = CompletionWitness(4)
    witness.payload_identity = identity
    call = witness.start("offline")
    witness.observe(call, {"choices": [{"finish_reason": "stop"}]})
    witness.end(call)
    data = healthy_report()
    return publication_result(data, judge_report(data, witness), witness)

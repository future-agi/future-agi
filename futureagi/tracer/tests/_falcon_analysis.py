"""Provider and ClickHouse boundaries for background Falcon worker tests."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx


def install_reader(monkeypatch, project, trace_id):
    reader = MagicMock()
    reader.root_ids_by_trace_ids.return_value = {
        trace_id: ("shared-span", str(project.pk))
    }
    reader.list_by_trace.return_value = [
        SimpleNamespace(
            id="shared-span",
            name="Own span",
            observation_type="llm",
            status="OK",
            model="test",
            latency_ms=1,
            total_tokens=1,
            parent_span_id=None,
            start_time=None,
            input="Own evidence",
            output="Own output",
            metadata={},
            span_events=[],
        )
    ]
    cm = MagicMock()
    cm.__enter__.return_value = reader
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: cm)
    return reader


def read_call(trace_id, span_id="shared-span"):
    return "read_trace_span", {"trace_id": trace_id, "span_id": span_id}


def tool_chunk(index, name, params):
    call = {
        "index": 0,
        "id": f"call-{index}",
        "function": {"name": name, "arguments": json.dumps(params)},
    }
    return {
        "choices": [{"delta": {"tool_calls": [call]}, "finish_reason": "tool_calls"}]
    }


def scripted_tool_stream(*tool_calls, fail_after_tool=False, finish="stop"):
    """Emit the scripted tool calls one turn at a time, then finish or interrupt."""
    turns = iter(enumerate(tool_calls))

    async def stream(messages, tools=None):
        turn = next(turns, None)
        if turn is not None:
            index, call = turn
            yield tool_chunk(index, *call)
        elif fail_after_tool:
            raise httpx.RemoteProtocolError("Interrupted after scoring")
        else:
            yield {
                "choices": [
                    {"delta": {"content": "Finished."}, "finish_reason": finish}
                ]
            }

    return stream


def score_params(trace_id):
    return {
        "trace_id": trace_id,
        "overall_score": 2.0,
        "factual_grounding_score": 2.0,
        "factual_grounding_reason": "Own score",
        "privacy_and_safety_score": 2.0,
        "privacy_and_safety_reason": "Own score",
        "instruction_adherence_score": 2.0,
        "instruction_adherence_reason": "Own score",
        "optimal_plan_execution_score": 2.0,
        "optimal_plan_execution_reason": "Own score",
        "insights": "Requesting project only",
        "recommended_priority": "MEDIUM",
    }


def finding_params(trace_id):
    return {
        "trace_id": trace_id,
        "category": "Thinking > Hallucination",
        "location_spans": ["shared-span"],
        "evidence_snippets": ["Own evidence"],
        "description": "Own finding",
        "impact": "MEDIUM",
        "root_causes": ["Own cause"],
        "recommendation": "Own fix",
        "confidence": 0.9,
    }

"""Stored tool-call input shared by Observe preview and trace eval execution."""

import json

from agentic_eval.metrics.tool_call_loop_repetition import validate_loop_window
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from tracer.services.clickhouse.v2.eval_loader import (
    EvalTelemetryReadError,
    _construct_from_chspan,
    _read_source,
)


def is_tool_call_loop_eval(template):
    return (template.config or {}).get("eval_type_id") == "ToolCallLoopRepetition"


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def trace_tool_call_input(trace, template, runtime_config):
    """Read the last configured window, never substitute PG for a failed CH read.

    A missing/incomplete recording is not evidence of healthy tool execution.
    Inputs with no JSON object shape are preserved under an ``input`` argument.
    Only completed tool spans are supported, not LLM tool-call proposals.
    """
    config = normalize_eval_runtime_config(template.config, runtime_config)
    params = config.get("params", {})
    window = validate_loop_window(
        params.get("window_size", 100), params.get("min_repeats", 3)
    )
    if _read_source() == "clickhouse":
        from tracer.services.clickhouse.v2 import get_reader

        try:
            with get_reader() as reader:
                spans = [
                    _construct_from_chspan(row)
                    for row in reader.recent_tool_spans(
                        str(trace.id), project_id=str(trace.project_id), limit=window
                    )
                ]
        except Exception as exc:
            raise EvalTelemetryReadError(
                "Tool-call telemetry could not be loaded. Please retry."
            ) from exc
    else:
        from tracer.models.observation_span import ObservationSpan

        spans = list(
            reversed(
                list(
                    ObservationSpan.objects.filter(
                        trace_id=trace.id,
                        project_id=trace.project_id,
                        deleted=False,
                        observation_type__iexact="tool",
                    ).order_by("-start_time", "-id")[:window]
                )
            )
        )
    if not spans:
        raise ValueError("This trace has no recorded tool calls to evaluate.")

    calls = []
    for span in spans:
        if (
            span.end_time is None
            or span.start_time is None
            or span.end_time < span.start_time
        ):
            raise ValueError(
                "Tool-call recording is incomplete. Wait for the trace to finish."
            )
        attrs = span.span_attributes or {}
        name = attrs.get("tool.name") or attrs.get("gen_ai.tool.name") or span.name
        arguments = _decode(attrs.get("gen_ai.tool.call.arguments", span.input))
        result = _decode(span.output)
        if arguments is None or result is None:
            raise ValueError(
                "Tool-call evaluation requires recorded inputs and outputs."
            )
        if not isinstance(arguments, dict):
            arguments = {"input": arguments}
        calls.append({"name": name, "arguments": arguments, "result": result})
    return {"output": calls}

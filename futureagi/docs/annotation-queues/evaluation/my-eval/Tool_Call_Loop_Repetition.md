---
title: Tool Call Loop Repetition
description: Local diagnostic scoring for repeated completed tool calls.
category: Local Metrics
tier: local
requires_llm: false
---

# Tool Call Loop Repetition

This evaluator diagnoses repeated tool-call sequences locally. It **does not
stop, cancel, retry, or execute tools**, and it installs no live Gateway plugin.
The incremental observer is diagnostic too; its historical `Guardrail` class
name does not imply enforcement. Live stopping requires a separate product
choice and integration.

## What it detects

- `exact_repeat`: the same tool, arguments and observed result repeat.
- `oscillation`: a short cycle repeats anywhere in a trace, including before a
  healthy final call.
- `near_duplicate`: the same tool repeats with similar string arguments and
  unchanged results. Semantic IDs, pagination values and non-string argument
  changes are not treated as cosmetic.

With result comparison enabled, **any changed result is treated as progress**,
including changes to small counters inside a large response. This conservative
rule avoids labelling healthy polling as a loop. Missing results can reveal a
repeated call pattern, but cannot establish whether the tool made progress.
Changing record `id` arguments is not ignored. Top-level timestamps, request
IDs and nonces are ignored by default; override the ignored-key set if those
fields carry meaning for your tools.

## Backend Python usage

These imports are provided by the backend source checkout, **not** by the
external `fi.evals` SDK. No SDK-level `evaluate("tool_call_loop_repetition", ...)`
calling convention is claimed.

```python
from agentic_eval.metrics.tool_call_loop_repetition import (
    evaluate_tool_call_loop_repetition,
)

calls = [
    {"name": "search_docs", "arguments": {"query": "refund policy"}, "result": []},
    {"name": "search_docs", "arguments": {"query": "refund policy"}, "result": []},
    {"name": "search_docs", "arguments": {"query": "refund policy"}, "result": []},
]
result = evaluate_tool_call_loop_repetition(calls)
assert result["loop_type"] == "exact_repeat"
assert result["passed"] is False
print(result["score"], result["reason"])
```

Input may also be a JSON string containing the list. The current Gateway's
OpenAI-compatible envelopes are supported:

```python
calls = [
    {
        "id": "call-1",
        "type": "function",
        "function": {"name": "poll", "arguments": '{"id": "job-1"}'},
        "result": {"progress": 1},
    },
]
```

Pass **completed** calls with their results when available. Flat calls accept
`tool_name`/`args`/`output` aliases. An explicit `result` wins over `output`,
even for `0`, `False`, `""`, empty containers, or `None`. Invalid names and
non-object arguments raise `ValueError` instead of receiving a healthy score.
The adapter consumes exported call data; it does not collect Gateway runs.

## Run it on an Observe trace

1. Record completed tool spans with the tracer: span kind `tool`, tool name
   (`tool.name`, `gen_ai.tool.name`, or the span name), input, output, and start/end
   timestamps. LLM tool-call proposals are not completed tool executions.
2. Open an evaluation task for the Observe project and choose **Traces** as its
   target. Add `tool_call_loop_repetition` from the evaluation picker.
3. Set **min_repeats** and **window_size** in the function parameters. No input
   mapping or model credentials are needed: the evaluator reads the selected
   trace's recorded tool calls automatically.
4. Run the live preview. Inspect the score and explanation, including the tool,
   repeated arguments, evaluated call count and configured window. Submit the
   task to evaluate the selected traces and retain the results.

Preview and the trace-task engine use the same input adapter and ClickHouse
recordings, including traces without a PostgreSQL copy. The legacy runner uses
its configured PostgreSQL read source. Reads are scoped to the trace's project
and ordered by start time then span ID, independently of arrival order. Only the
most recent `window_size` tool calls are inspected; older loops outside the
window are not reported. Missing calls, missing inputs/outputs, unfinished spans,
and failed reads produce an error rather than a healthy empty score.

This is a **built-in deterministic function eval**, rather than a custom-code
example: the picker, parameter controls, trace tasks and result display already
provide the needed lifecycle, without duplicating code in a sandbox or adding a
model call. It does not change Gateway execution. Use a trace target, not a span,
session or dataset target; an individual span does not contain the call sequence.

The registered type is `ToolCallLoopRepetition`. Existing backend callers can
still supply an explicit `output` list of completed calls. Stored-trace execution
always reads recorded spans instead of trusting a supplied output summary.
Scores range from 0 to 1 (higher is healthier), with a 0.5 pass threshold. A
recorded loop falls below that threshold; progress-sensitive calls score 1.
Use the standalone helper above for structured cycle diagnostics. A low score is
**not** an instruction to interrupt an agent.

## Incremental diagnostic observation

```python
from agentic_eval.metrics.tool_call_loop_repetition import ToolCall, ToolCallLoopGuardrail

observer = ToolCallLoopGuardrail(window_size=12, min_repeats=3)
for progress in range(4):
    diagnostic = observer.observe(
        ToolCall("poll", {"id": "job-1"}, result={"progress": progress})
    )
    assert not diagnostic.loop_detected
```

Create one observer per sequential run; it is not a shared concurrent collector.
The window bounds retained calls. Indices remain full-stream positions after
trimming; explicit `ToolCall.index` values take precedence. `reset()` clears the
buffer and restarts implicit indices at zero. Once an earlier loop leaves the
window, it is no longer reported by that observer; evaluation with a larger
window can still find it. A window must hold at least `min_repeats` calls, and must be large
enough for any longer cycle you want to diagnose.

## Configuration and limits

| Parameter | Default | Meaning |
|---|---|---|
| `min_repeats` | `3` | Minimum complete repetitions, integer from 2 to 1000 |
| `window_size` | `100` | Most recent calls to inspect, at least `min_repeats` and at most 1000 |
| `max_cycle_length` | `4` | Maximum cycle length, positive integer |
| `near_duplicate_threshold` | `0.92` | String similarity threshold between 0 and 1; 1 requires exact strings |
| `also_compare_results` | `True` | Changed results are progress; disabling this diagnoses call shape only |
| `ignored_argument_keys` | volatile top-level keys | Standalone detector/helper option; replaces the default ignored-key set |

There is no network or model call in the detector. Runtime depends on trace
length, cycle limit, and argument/result size; there is **no universal sub-10 ms
latency guarantee**. Exact cycle search scans all positions in
`O(number_of_calls × max_cycle_length)` after normalization. Similarity checks
on long strings can cost more. This heuristic cannot determine whether a
changing result is useful progress, or whether identical responses are an
expected part of a workflow; use the diagnostics as evidence, not enforcement.

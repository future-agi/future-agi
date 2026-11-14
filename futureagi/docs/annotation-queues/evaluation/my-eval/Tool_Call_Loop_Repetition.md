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

## Registered backend evaluator

The catalog contains the hidden diagnostic template `tool_call_loop_repetition`
with type `ToolCallLoopRepetition`, required input `output`, and score output.
It is registered with the backend function-evaluation engine and can be invoked
through existing evaluation APIs with an explicit template/type selection.
It is not added to the UI picker while product behavior is being settled.

The standard backend evaluator result contains the score and reason. Use the
standalone helper above for structured cycle diagnostics. Scores range from
0 to 1, where higher is healthier; the diagnostic pass threshold is 0.5.
A low score is **not** an instruction to interrupt an agent.

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
window, it is no longer reported by that observer; full-trace evaluation can
still find it. A window must hold at least `min_repeats` calls, and must be large
enough for any longer cycle you want to diagnose.

## Configuration and limits

| Parameter | Default | Meaning |
|---|---|---|
| `min_repeats` | `3` | Minimum complete repetitions, integer at least 2 |
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

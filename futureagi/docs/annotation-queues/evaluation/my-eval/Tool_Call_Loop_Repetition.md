---
title: Tool Call Loop Repetition
description: Detects agents stuck repeating or oscillating between the same tool calls without making progress.
category: Local Metrics / Guardrails
tier: local
latency: "< 10ms"
requires_llm: false
---

# Tool Call Loop Repetition

Detects when an agent is stuck in a non-terminating loop: calling the same
tool over and over, oscillating between a small cycle of tool calls, or
retrying near-duplicate calls that never really change state. This is one of
the most common and expensive agentic failure modes in production — it burns
tokens and API spend and leaves a workflow stuck — and it's a **local
metric**: no LLM judge call is required, so it's safe to run inline as a
guardrail as well as post-hoc over stored traces.

## Why you'd use this

- Catch runaway agent loops before they burn through your token/API budget.
- Surface stuck workflows in the trace inspector so you know *where* an agent
  got stuck, not just that the run took too long.
- Wire it into the gateway as a real-time guardrail to interrupt a looping
  agent mid-run instead of waiting for the full trace.

## What it detects

| Loop type | Example |
|---|---|
| `exact_repeat` | The same tool + same arguments called 3+ times in a row |
| `oscillation` | A short cycle (e.g. `search → read_faq → search → read_faq`) repeating 3+ times |
| `near_duplicate` | Same tool called repeatedly with only cosmetic argument differences (e.g. a slightly reworded query) and no meaningfully new result |

It deliberately does **not** flag legitimate iterative patterns like
pagination or polling, where arguments may look similar but the tool's
*results* keep changing — that's treated as real progress, not a loop.

## Usage

```python
from fi.evals import evaluate

result = evaluate(
    "tool_call_loop_repetition",
    tool_calls=[
        {"name": "search_docs", "arguments": {"query": "refund policy"}},
        {"name": "search_docs", "arguments": {"query": "refund policy"}},
        {"name": "search_docs", "arguments": {"query": "refund policy"}},
    ],
)

print(result.score)   # 0.0-1.0, higher = healthier (no loop)
print(result.passed)  # False once repeats cross the min_repeats threshold
print(result.reason)  # human-readable explanation for the trace inspector
```

### Real-time guardrail (gateway / inline use)

```python
from fi.evals.guardrails import ToolCallLoopGuardrail

guardrail = ToolCallLoopGuardrail(min_repeats=3)

for call in agent_tool_calls:
    result = guardrail.observe(call)
    if result.loop_detected:
        # interrupt the agent, surface a warning, or fall back to a human
        break
```

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `min_repeats` | `3` | Minimum consecutive cycle repetitions before flagging a loop |
| `max_cycle_length` | `4` | Longest call-cycle searched for (covers single-call retries through 4-step oscillations) |
| `near_duplicate_threshold` | `0.92` | Similarity (0-1) above which two calls' arguments count as "the same call" |
| `ignored_argument_keys` | timestamps, request/trace ids, nonces | Volatile keys ignored when comparing call arguments |
| `also_compare_results` | `True` | If results genuinely diverge across similar calls, don't treat it as a stuck loop |

## Score interpretation

Score is in `[0, 1]`, where `1.0` means no repetitive pattern was found and
lower scores indicate increasingly severe loops (more repeats / tighter
cycles). `passed` defaults to `score >= 0.5`, matching the pass/fail
convention used by this project's other guardrail-style metrics (e.g.
toxicity).

# Auto Eval Agent — Backend Integration Guide

This document is for the BE engineer wiring the Auto Eval Agent into the production backend.
The UX entry point (toggle) is still being designed; this covers everything you need on the BE side.

---

## What it does

The Auto Eval Agent is a two-stage agentic pipeline that evaluates a single data point
(span, trace, session, or dataset row) against a natural-language criteria, without
requiring a fixed eval template mapping.

```
EvalScout (Haiku, 1 call)
    → analyses criteria + input summary + available resources
    → produces a structured "brief" (complexity, model tier, resource plan)

EvalOrchestrator (Sonnet, tool-use loop, up to 15 turns)
    → uses tools to investigate the data (drill down, KB search, feedback retrieval)
    → calls run_eval (single LLM call) at the right model tier
    → retries if confidence is low (max 3 eval calls)
    → calls submit_result to finalize
```

---

## Inputs you need to provide

Before wiring the pipeline, understand what each input is and where it comes from:

| Input | What it is | Where you get it |
|-------|-----------|-----------------|
| `organization_id` | The org UUID | From request context (`request.org.id`) |
| `criteria` | Natural-language eval criteria, e.g. "Is the response polite?" | User enters this in the UI when setting up auto eval |
| `input_scope` | What type of data is being evaluated | Determined by what the user is evaluating (see table below) |
| `source_id` | The primary key of the specific object being evaluated | The ID of the cell/row/span/trace/session the user wants to evaluate |
| `choices` | The possible output labels, e.g. `["Passed", "Failed"]` | User configures this in the UI. `None` = scorer mode (0.0–1.0) |
| `eval_template_id` | UUID of the `EvalTemplate` (from `model_hub_evaltemplate`) | `None` on first run. After the first run, the BE creates an EvalTemplate and passes its ID here on subsequent runs so the agent can retrieve past human feedback |
| `kb_id` | UUID of a `KnowledgeBaseFile` (from `model_hub_knowledgebasefile`) | `None` if no KB is attached. If the user has connected a KB to this eval, pass its UUID here |

### What is `source_id`?

`source_id` is the primary key of the data being evaluated. It changes meaning based on `input_scope`:

| `input_scope` | `source_id` is | Example | DB table |
|---------------|---------------|---------|----------|
| `"span"` | A span ID (hex string) | `"52f9f7958b644085"` | `tracer_observationspan` |
| `"trace"` | A trace UUID | `"acfd4b13-f169-..."` | `tracer_trace` |
| `"session"` | A session UUID | `"e6f1003f-b750-..."` | `tracer_tracesession` |
| `"dataset_row"` | A Row UUID | `"a97cd808-3e03-..."` | `model_hub_row` |
| `"cell"` | A Cell UUID | `"b12cd808-4e14-..."` | `model_hub_cell` |

The agent uses `source_id` + `input_scope` to fetch the actual data from the database. For example, if `input_scope="span"` and `source_id="52f9f7958b644085"`, the agent queries `ObservationSpan.objects.get(id="52f9f7958b644085")` to read the span's input/output. If `input_scope="cell"`, it fetches a single cell's value along with its column name.

---

## Step-by-step integration

### Step 1. Gather available resources

Checks what external resources (KB, feedback, MCP tools) are connected for this org.
Call this first — it only needs plain IDs, no EvalConfig.

```python
from agentic_eval.agenthub.eval_orchestrator.utils import gather_available_resources

available_resources = gather_available_resources(
    organization_id=str(org.id),        # from request context
    eval_template_id=eval_template_id,  # None on first run (no feedback yet)
    kb_id=kb_id,                        # None if no KB connected
)
# Returns:
# {
#     "knowledge_bases": [{"id": "...", "name": "...", "description": "..."}],
#     "feedback_count":  12,   # number of human feedback examples in ClickHouse
#     "mcp_tools":       [{"name": "...", "description": "..."}],
# }
```

### Step 2. Build EvalConfig

```python
from agentic_eval.agenthub.eval_orchestrator.orchestrator import EvalConfig

eval_config = EvalConfig(
    criteria=criteria,                    # user-entered eval criteria string
    input_scope=input_scope,              # "span" | "trace" | "session" | "dataset_row" | "cell"
    source_id=source_id,                  # ID of the object being evaluated
    choices=choices,                      # ["Passed", "Failed"], custom labels, or None for scorer
    eval_template_id=eval_template_id,    # None on first run
    kb_id=kb_id,                          # None if no KB connected
    available_resources=available_resources,
)
```

### Step 3. Build input summary (for Scout)

```python
from agentic_eval.agenthub.eval_orchestrator.utils import build_input_summary

input_summary = build_input_summary(scope=input_scope, source_id=source_id)
```

A lightweight text summary of the data. The orchestrator drills into the actual DB data using its tools — this is just for the Scout's quick triage.

### Step 4. Run Scout (1 cheap LLM call)

```python
from agentic_eval.agenthub.eval_orchestrator.scout import EvalScout

scout = EvalScout()
brief = scout.run(
    criteria=criteria,
    input_summary=input_summary,
    available_resources=available_resources,
)
brief["available_resources"] = available_resources  # must be attached to brief
```

### Step 5. Run Orchestrator (tool-use loop)

```python
from agentic_eval.agenthub.eval_orchestrator.orchestrator import EvalOrchestrator

orch = EvalOrchestrator(eval_config=eval_config, scout_brief=brief)
result = orch.run()
```

---

## Full copy-paste example

```python
from agentic_eval.agenthub.eval_orchestrator.orchestrator import EvalConfig, EvalOrchestrator
from agentic_eval.agenthub.eval_orchestrator.scout import EvalScout
from agentic_eval.agenthub.eval_orchestrator.utils import gather_available_resources, build_input_summary

# 1. Gather resources
available_resources = gather_available_resources(
    organization_id=str(org.id),
    eval_template_id=eval_template_id,
    kb_id=kb_id,
)

# 2. Build config
eval_config = EvalConfig(
    criteria=criteria,
    input_scope=input_scope,
    source_id=source_id,
    choices=choices,
    eval_template_id=eval_template_id,
    kb_id=kb_id,
    available_resources=available_resources,
)

# 3. Build input summary
input_summary = build_input_summary(scope=input_scope, source_id=source_id)

# 4. Run Scout
brief = EvalScout().run(
    criteria=criteria,
    input_summary=input_summary,
    available_resources=available_resources,
)
brief["available_resources"] = available_resources

# 5. Run Orchestrator
result = EvalOrchestrator(eval_config=eval_config, scout_brief=brief).run()
```

---

## Reference

### EvalConfig fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `criteria` | `str` | Yes | Natural-language eval criteria (can be vague) |
| `input_scope` | `str` | Yes | `"span"` / `"trace"` / `"session"` / `"dataset_row"` / `"cell"` |
| `source_id` | `str` | Yes | UUID/ID of the object being evaluated |
| `choices` | `list \| None` | No | See "Choice types" below. `None` → scorer mode. |
| `eval_template_id` | `str \| None` | No | EvalTemplate UUID for feedback retrieval. `None` on first run. |
| `kb_id` | `str \| None` | No | KnowledgeBaseFile UUID if a KB is connected |
| `available_resources` | `dict` | No | From `gather_available_resources()` |

The agent derives `organization_id` internally from `source_id` + `input_scope` — no need to pass it.

### Choice types (same as deterministic evaluator)

| Type | `choices` value | Behavior |
|------|----------------|----------|
| Binary | `["Passed", "Failed"]` | Pass/fail classification |
| Scorer | `None` or `[]` | Defaults to `["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]`. Result is a float 0–1. |
| Custom labels | `["label1", "label2", ...]` | User-defined categories |

### input_scope → source_id mapping

| `input_scope` | `source_id` | What the agent reads |
|---------------|-------------|----------------------|
| `"span"` | span_id (hex string) | Single span |
| `"trace"` | trace_id (UUID) | All spans in the trace |
| `"session"` | session_id (UUID) | All traces in the session |
| `"dataset_row"` | Row UUID | All cells in the row |
| `"cell"` | Cell UUID | Single cell value + column name |

---

## Output

`orch.run()` returns:

```python
{
    "result": {
        "result":      str,    # one of the values from choices[], e.g. "Passed"
        "confidence":  float,  # 0.0–1.0
        "explanation": str,    # human-readable reasoning
    },
    "model_used":           "flash" | "small" | "large",
    "eval_calls":           int,   # how many run_eval calls were made (1–3)
    "resources_used":       [str], # e.g. ["kb", "feedback"]
    "drill_down_performed": bool,
    "orchestrator_turns":   int,
    "token_usage": {
        "scout":        {"prompt_tokens": int, "completion_tokens": int},
        "formalization": {"prompt_tokens": int, "completion_tokens": int},
        "orchestrator": {"prompt_tokens": int, "completion_tokens": int},
        "eval_calls":   {"prompt_tokens": int, "completion_tokens": int},
        "total":        {"prompt_tokens": int, "completion_tokens": int},
    },
}
```

---

## APICallLog — caller's responsibility

The agent itself does **not** create an `APICallLog`. The caller must create it,
following the exact same pattern as `_handle_api_call` in `eval_runner.py` does for
the deterministic agent.

**Why it's needed:** `EvalPlayGroundFeedbackAPIView` (the human feedback endpoint)
reads `APICallLog.config["mappings"]` to know which fields the eval ran against,
so it can store feedback in ClickHouse under the right keys.

**Config structure to use when building the log:**

For `dataset_row` scope — use raw column names from the Row:
```python
config["mappings"] = {
    "Column 1": "<cell_value>",
    "required_keys": ["Column 1", ...]
}
```

For `cell` scope — use the cell's column name:
```python
config["mappings"] = {
    "<column_name>": "<cell_value>",
    "required_keys": ["<column_name>"],
}
```

For `span` / `trace` / `session` scope — use `input` / `output`:
```python
config["mappings"] = {
    "input":  "<span input>",
    "output": "<span output>",
    "required_keys": ["input", "output"],
}
```

`log.source_id` must be set to the `eval_template_id` — this is what
`data_formatter` uses as `eval_id` when writing feedback to ClickHouse.

---

## Where this differs from the existing deterministic eval flow

| | Deterministic eval | Auto eval |
|---|---|---|
| Entry point | `EvalRunnerView` → `eval_runner.py` | New view/task (TBD — toggled per project/dataset) |
| Eval template | Required, user-mapped | Optional — only for feedback retrieval, no field mapping needed |
| Input mapping | `UserEvalMetric.config["mapping"]` maps template keys → column UUIDs | Agent reads raw data directly from DB |
| APICallLog | Created in `_handle_api_call` before eval runs | Created by caller before running Scout+Orchestrator |
| Result storage | Written to `Cell` by `_create_cell` | Caller writes result however the toggle UX decides |
| Feedback add | Via `EvalPlayGroundFeedbackAPIView` using `log_id` | Same endpoint, same flow — no changes needed |

---

## Running integration tests

```bash
docker exec backend bash -c \
    "cd /app/backend && PYTHONPATH=/app/backend \
     python agentic_eval/agenthub/eval_orchestrator/run_real_integration_tests.py"

# Run a specific case:
... run_real_integration_tests.py --filter "Feedback-1"
```

Results are written to `agentic_eval/agenthub/eval_orchestrator/real_test_results.txt`.

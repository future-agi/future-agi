<div align="center">

# Tool-Use Verification and Coding-Agent Simulation

**Multi-step F1 with schema checks plus SWE-bench style test execution.**

[![tool-f1](https://img.shields.io/badge/ToolCallF1-order--insensitive-blue?style=flat-square)](./tool-coding-agents.md)
[![efficiency](https://img.shields.io/badge/trajectory-efficiency-green?style=flat-square)](./tool-coding-agents.md)
[![coding](https://img.shields.io/badge/coding-pytest-lightgrey?style=flat-square)](./tool-coding-agents.md)

</div>

---

## Contents

- [Overview](#overview)
- [Evaluators](#evaluators)
- [Coding Adapter](#coding-adapter)
- [Usage](#usage)
- [Verification](#verification)
- [Benchmarks](#benchmarks)
- [Reviewer Guide](#reviewer-guide)

---

## Overview

`ToolCallAccuracy` scores single calls with exact match. It misses reordered parallel calls, argument type errors, and trajectory bloat. Coding agents also need more than static checks: `SyntaxValidation` and `CodeBleu` pass plausible but wrong patches.

This change adds two trajectory evaluators plus a local coding-agent adapter that applies file operations and runs `pytest` in a subprocess with timeout.

> [!NOTE]
> No LLM and no GPU required. Tool scoring is deterministic string and dict math. Coding runs use the local `pytest` binary.

## Evaluators

### 1. ToolCallF1

- **Purpose:** precision and recall over a whole tool-call trajectory with partial credit.
- **Inputs:** `output` and `expected` as JSON arrays of `{name, arguments}`, plus optional `schemas` map of tool name to JSON schema.
- **Output:** F1 in `[0, 1]` with `P`, `R`, exact count, name-only count, and schema violations.
- **Implementation:** `futureagi/agentic_eval/core_evals/fi_evals/function/functions.py` (`calculate_tool_call_f1`).

```python
trajectory = [
    {"name": "get_weather", "arguments": {"city": "Paris"}},
    {"name": "book_flight", "arguments": {"to": "Paris", "seats": 2}},
]

calculate_tool_call_f1(output=trajectory, expected=trajectory)
# {'result': 1.0, 'reason': 'ToolCallF1: 1.0000 (P=1.000 R=1.000, 2 exact ...) ...'}

calculate_tool_call_f1(
    output=list(reversed(trajectory)),
    expected=trajectory,
)
# {'result': 1.0, ...}  # order-insensitive
```

```text
perfect    -> 1.00 (2 exact)
reordered  -> 1.00 (greedy matching, partial credit)
wrong args -> 0.50 to 0.75 (name match only)
```

Schema validation:

```python
schemas = {
    "book_flight": {
        "type": "object",
        "required": ["to", "seats"],
        "properties": {"to": {"type": "string"}, "seats": {"type": "integer"}},
    }
}

calculate_tool_call_f1(
    output=[{"name": "book_flight", "arguments": {"to": "Paris", "seats": "two"}}],
    expected=[{"name": "book_flight", "arguments": {"to": "Paris", "seats": 2}}],
    schemas=schemas,
)
# {'result': lower, 'reason': '... 1 schema violations: book_flight: arg seats should be integer'}
```

### 2. TrajectoryEfficiency

- **Purpose:** reward short correct paths and state changes.
- **Inputs:** `output` and `expected` as action lists or `{actions, before, after}` dicts, plus `optimal_steps` override.
- **Output:** `0.5 * efficiency + 0.3 * overlap + 0.2 * state`, where efficiency is `optimal / actual` capped at 1.
- **Implementation:** `calculate_trajectory_efficiency` in the same `functions.py`.

```python
calculate_trajectory_efficiency(
    output=["search", "patch", "test"],
    expected=["search", "patch", "test"],
    optimal_steps=3,
)
# {'result': 0.95+, ...}
```

```text
optimal (3/3) -> 0.90+
bloated (7/3) -> lower efficiency term
state match   -> bonus when after-state equals wanted state
```

## Coding Adapter

`futureagi/simulate/services/coding_agent_adapter.py` applies allowlisted file operations to a repo copy and runs `pytest`:

```python
from simulate.services.coding_agent_adapter import apply_tool_calls

result = apply_tool_calls(
    "/tmp/repo-copy",
    [{"name": "write_file", "arguments": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}}],
    timeout=30,
)
# {'applied': ['calc.py'], 'passed': 1, 'total': 1, 'score': 1.0, ...}
```

```text
applied: ['calc.py']
tests: 1 passed / 1 total
score: 1.00
```

Supported actions: `write_file`, `patch_file`, `run_tests`, `read_file`. Path traversal with `..` is rejected. Unknown actions are skipped.

New agent type:

```python
class AgentTypeChoices(models.TextChoices):
    VOICE = "voice", "Voice"
    TEXT = "text", "Text"
    CODING = "coding", "Coding"
```

Migration `futureagi/simulate/migrations/0079_agentdefinition_coding_type.py` alters `agent_type` choices. Unlike the CUA stub, `CODING` is runnable through the adapter.

> [!TIP]
> Keep coding tasks to one function plus 3 to 5 pytest cases. Use the adapter timeout of 30s for unit tasks.

## Usage

```python
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_tool_call_f1,
    calculate_trajectory_efficiency,
)
from agentic_eval.core_evals.fi_evals.function.wrapper import (
    ToolCallF1,
    TrajectoryEfficiency,
)

ev1 = ToolCallF1(schemas={})
ev2 = TrajectoryEfficiency(optimal_steps=3)
```

UI catalog:

```yaml
# futureagi/model_hub/system_evals/function/tool_call_f1.yaml
eval_id: 202
name: tool_call_f1
config:
  required_keys: [output, expected]
  output: score
```

## Verification

```bash
python scripts/verify_tool_coding.py
```

```text
ToolCallF1 perfect: 1.0
ToolCallF1 reordered: 1.0
TrajectoryEfficiency optimal: 0.95+
Coding adapter: 1.0 ['calc.py'] 1/1
OVERALL PASS
```

Unit tests:

```bash
python -m pytest futureagi/agentic_eval/tests/test_tool_coding.py -v -m "not live_llm"
```

Expected: 14 passed. Covers perfect match, reordered credit, wrong-args partial, schema penalty, empty trajectories, efficiency optimal versus bloated, state bonus, adapter write plus test run, and path-traversal rejection.

```bash
python -c "import yaml, pathlib; [yaml.safe_load(open(p)) for p in pathlib.Path('futureagi/model_hub/system_evals/function').glob('*.yaml')]; print('YAML OK')"
```

## Benchmarks

| Case | ToolCallAccuracy (single-call) | ToolCallF1 (this PR) |
| :--- | :---: | :---: |
| Perfect 2-step | 1.00 | **1.00** |
| Reordered 2-step | 0.50 | **1.00** |
| Wrong args, right names | 0.50 | **0.50 to 0.75 with schema note** |
| Extra spurious call | Penalized harshly | **Precision-aware F1** |

| Case | SyntaxValidation | CodeBleu | Coding adapter (this PR) |
| :--- | :---: | :---: | :---: |
| Correct patch | Pass | High | **1.00 (tests pass)** |
| Plausible wrong patch | Pass | High | **0.00 to 0.50 (tests fail)** |

> [!IMPORTANT]
> Execution separates correct from plausible patches where static scores tie.

## Reviewer Guide

Check core files:

- `futureagi/agentic_eval/core_evals/fi_evals/function/functions.py` (ToolCallF1, TrajectoryEfficiency)
- `futureagi/agentic_eval/core_evals/fi_evals/eval_type.py` (enum entries)
- `futureagi/agentic_eval/core_evals/fi_evals/function/wrapper.py` (wrappers)
- `futureagi/agentic_eval/core_evals/fi_evals/__init__.py` (exports)
- `futureagi/model_hub/system_evals/function/tool_call_f1.yaml` (eval_id `202`)
- `futureagi/model_hub/system_evals/function/trajectory_efficiency.yaml` (eval_id `203`)
- `futureagi/evaluations/catalog/system_evals.yaml` and `system_eval_code.py` (engine catalog)
- `futureagi/simulate/models/agent_definition.py` (CODING choice)
- `futureagi/simulate/migrations/0079_agentdefinition_coding_type.py` (migration)
- `futureagi/simulate/services/coding_agent_adapter.py` (adapter)

Run verification:

```bash
python scripts/verify_tool_coding.py
python -m pytest futureagi/agentic_eval/tests/test_tool_coding.py -v -m "not live_llm"
```

<div align="center">

**Verified trajectories. Executed patches. Ready to review.**

</div>

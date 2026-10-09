<div align="center">

# Code Execution: Pass Rate and Safety for Generated Code

**Run the code, not just parse it. Isolated subprocess execution with timeout guard.**

[![execution](https://img.shields.io/badge/execution-subprocess-brightgreen?style=flat-square)](./code-execution.md)
[![safety](https://img.shields.io/badge/safety-static--scan-blue?style=flat-square)](./code-execution.md)
[![tests](https://img.shields.io/badge/tests-14%20passed-success?style=flat-square)](./code-execution.md)

</div>

---

## Contents

- [Overview](#overview)
- [Evaluators](#evaluators)
- [Usage](#usage)
- [Verification](#verification)
- [Benchmarks](#benchmarks)
- [Reviewer Guide](#reviewer-guide)

---

## Overview

Static checks (`SyntaxValidation`, `CodeComplexity`, `CodeBleu`) cannot tell correct code from plausible but wrong code. Both score well on syntax and token overlap.

This change adds execution: candidate Python code runs against supplied tests in an isolated `python3 -I` subprocess with a clamped timeout. Infinite loops fail closed. A companion static scan flags risky patterns with no extra dependencies.

> [!NOTE]
> No Docker and no GPU required. Candidate code runs locally in one subprocess per evaluation with `timeout + 2` guard.

## Evaluators

### 1. CodeExecutionPass

- **Purpose:** pass rate of candidate code over executable tests.
- **Inputs:** `output` (candidate Python defining the function), `expected` (tests as JSON array, `func` plus `cases` object, or assert lines), `timeout` (default `5`, clamped to `1..30`).
- **Output:** `passed / total` plus per-case details.
- **Implementation:** `futureagi/agentic_eval/core_evals/fi_evals/function/functions.py` (`calculate_code_execution_pass`, `_run_candidate_tests`, `_parse_code_tests`).

```python
code = "def add(a, b):\n    return a + b\n"
tests = [
    {"func": "add", "args": [2, 3], "expected": 5},
    {"func": "add", "args": [-1, 1], "expected": 0},
]

calculate_code_execution_pass(output=code, expected=tests)
# {'result': 1.0, 'reason': 'Code Execution Pass: 1.0000 (3/3 ...) ...'}

calculate_code_execution_pass(
    output="def add(a, b):\n    return a - b\n",
    expected=tests,
)
# {'result': 0.333..., 'reason': '... case 0: FAIL ...'}
```

```text
correct  -> 1.00 (3/3 passed)
buggy    -> 0.33 (1/3 passed, subtraction breaks two cases)
syntax error -> 0.00 (no tests executed)
infinite loop -> 0.00 (timeout after 1s)
```

Test shapes accepted:

```python
# JSON array (preferred)
[{"func": "add", "args": [2, 3], "expected": 5}]

# Assert lines
"assert add(2, 3) == 5\nassert add(0, 0) == 0"
```

### 2. CodeSafety

- **Purpose:** lightweight Bandit-style static scan with no dependencies.
- **Inputs:** `output` (candidate code).
- **Output:** `1.0` when clean, minus `0.25` per finding (floored at `0.0`).
- **Implementation:** `calculate_code_safety` with `_BLOCKED_CODE_PATTERNS`.

```python
calculate_code_safety(output="def add(a, b):\n    return a + b\n")
# {'result': 1.0, 'reason': 'Code Safety: 1.0000 (no risky patterns)'}

calculate_code_safety(output="import subprocess\nsubprocess.run(['ls'])")
# {'result': 0.75, 'reason': 'Code Safety: 0.7500 (flagged: subprocess)'}
```

Flagged patterns: `os.system`, `subprocess`, `socket`, `eval`, `exec`, `__import__`, `open`, `input`, `compile`.

## Usage

```python
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_code_execution_pass,
    calculate_code_safety,
)
from agentic_eval.core_evals.fi_evals.function.wrapper import (
    CodeExecutionPass,
    CodeSafety,
)

ev1 = CodeExecutionPass(timeout=5)
ev2 = CodeSafety()
```

UI catalog entries:

```yaml
# futureagi/model_hub/system_evals/function/code_execution_pass.yaml
eval_id: 205
name: code_execution_pass
config:
  required_keys: [output, expected]
  output: score
```

> [!TIP]
> Keep candidate code to a single function plus helpers. Provide at least 3 cases including edges such as zeros and negatives.

## Verification

```bash
python scripts/verify_code_execution.py
```

```text
correct code: 1.0 Code Execution Pass: 1.0000 (3/3 passed ...)
buggy code: 0.333 Code Execution Pass: 0.3333 (1/3 passed ...)
safety clean: 1.0 risky: 0.75
infinite loop: 0.0 timeout after 1s
OVERALL PASS
```

Unit tests:

```bash
python -m pytest futureagi/agentic_eval/tests/test_code_execution.py -v -m "not live_llm"
```

Expected: 14 passed. Covers full pass, partial pass, syntax error, empty code, missing tests, JSON string tests, assert style, timeout guard, wrapper creation, determinism, and safety flags.

```bash
python -c "import yaml, pathlib; [yaml.safe_load(open(p)) for p in pathlib.Path('futureagi/model_hub/system_evals/function').glob('*.yaml')]; print('YAML OK')"
```

## Benchmarks

| Case | SyntaxValidation | CodeBleu (static) | CodeExecutionPass (this PR) |
| :--- | :---: | :---: | :---: |
| Correct `add` | Pass | High | **1.00 (3/3)** |
| Buggy `add` (subtraction) | Pass | High | **0.33 (1/3)** |
| Syntax error | Fail | Low | **0.00 (no run)** |
| Infinite loop | Pass | High | **0.00 (timeout)** |

Static scores tie on correct versus buggy. Execution separates them.

## Reviewer Guide

Check core files:

- `futureagi/agentic_eval/core_evals/fi_evals/function/functions.py` (evaluators and runner)
- `futureagi/agentic_eval/core_evals/fi_evals/eval_type.py` (enum entries)
- `futureagi/agentic_eval/core_evals/fi_evals/function/wrapper.py` (wrappers)
- `futureagi/agentic_eval/core_evals/fi_evals/__init__.py` (exports)
- `futureagi/model_hub/system_evals/function/code_execution_pass.yaml` (eval_id `205`)
- `futureagi/model_hub/system_evals/function/code_safety.yaml` (eval_id `206`)
- `futureagi/evaluations/catalog/system_evals.yaml` and `system_eval_code.py` (engine catalog)

Run verification:

```bash
python scripts/verify_code_execution.py
python -m pytest futureagi/agentic_eval/tests/test_code_execution.py -v -m "not live_llm"
```

<div align="center">

**Executed, not guessed. Ready to review.**

</div>

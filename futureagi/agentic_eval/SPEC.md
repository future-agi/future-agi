# agentic_eval — Specification

`agentic_eval` is the **evaluator implementation layer**. It hosts all 95+ evaluator
class implementations. The `evaluations/engine/` orchestration layer imports from here —
`agentic_eval` has no knowledge of Django ORM, templates, or the eval runner.

```
evaluations/engine/registry.py
    → imports agentic_eval.core_evals.fi_evals.__all__
    → registers each class by its .name property

evaluations/engine/runner.run_eval()
    → registry.get_eval_class(eval_type_id)
    → instance.create_eval_instance(eval_class, ...)
    → eval_instance.run(**params)          # Returns BatchRunResult
    → formatting.extract_raw_result(...)   # Extracts EvalResult from eval_results[0]
```

---

## The Evaluator Protocol

All evaluators must satisfy this interface, defined in `base_evaluator.py`:

### Abstract properties

| Property | Type | Contract |
|----------|------|---------|
| `name` | `str` | Stable identifier, matches `eval_type_id` in the registry |
| `display_name` | `str` | Human-readable label |
| `metric_ids` | `list[str]` | Names of metrics this evaluator computes |
| `required_args` | `list[str]` | Keys that must be present in `run()` kwargs |
| `examples` | any | Sample inputs (may be `None`) |

### Methods

**`run(**kwargs) → BatchRunResult`** — single evaluation. Calls `_evaluate(**kwargs)` and
wraps the result.

**`run_batch(data: list[DataPoint], max_parallel_evals=5, upload_to_fi=True) → BatchRunResult`**
— parallel evaluation via `ThreadPoolExecutor`. Errors per item produce `None` in
`eval_results`, not exceptions.

**`_evaluate(**kwargs) → EvalResult`** — abstract, implemented by each subclass.

**`guard(**kwargs) → GuardResult`** — guard mode: returns `GuardResult(passed, reason, runtime)`.

**`is_failure(*args) → bool | None`** — abstract. Whether a given raw result counts as failure.

### `EvalResult` (TypedDict)

```python
{
    "name":       str,                         # evaluator.name
    "display_name": str,
    "data":       dict,                        # echo of inputs
    "failure":    bool | None,                 # True = failed, None = inconclusive
    "reason":     str,                         # explanation or error message
    "runtime":    int,                         # milliseconds
    "model":      str | None,
    "metadata":   str | None,                  # JSON-serialised dict (see ADR 021)
    "metrics":    list[{"id": str, "value": float}],
    "datapoint_field_annotations": list | None,
}
```

### `BatchRunResult` (dataclass)

```python
@dataclass
class BatchRunResult:
    eval_results: list[EvalResult | None]  # None means an exception was swallowed
    eval_request_id: str | None = None
```

**Invariant:** `evaluations/engine/formatting.extract_raw_result()` reads
`raw_result.eval_results[0]`. If the list is empty, it returns an empty dict and the
eval is treated as failed with no reason.

---

## Evaluator Categories

### LLM-Based

**`CustomPromptEvaluator`** (open-source)

The most-used evaluator. Runs an LLM with a Jinja2/Mustache template prompt.

Constructor key params:
- `rule_prompt` — template string with `{{variable}}` placeholders
- `output_type` — `"Pass/Fail"` / `"score"` / `"choices"` / `"numeric"`
- `model` — required LLM model name
- `choices` — list of valid choice strings (for `output_type="choices"`)

`_evaluate()` pipeline:
1. Validate required keys exist in inputs.
2. Truncate values >15KB via `fit_to_context()` — **silent truncation, no feedback** (see issue #316).
3. Render template: Jinja2 with `PreserveUndefined` (missing vars render as `{{var}}`, not error — see issue #317).
4. Detect multimodal content (images, audio, PDFs) and build media blocks.
5. Build message chain: system prompt → few-shot examples → optional `messages` turns → main eval message.
6. Route LLM call: Turing models → `TuringClient`; external models → `call_llm()` via provider.
7. Parse JSON response: extract `"result"` and `"explanation"` fields.
8. Return `EvalResult` with `metadata` = JSON-serialised `{usage, cost, response_time, explanation}`.

**Note:** `CustomPromptEvaluator` inherits from `LLM`, not `BaseEvaluator` — see ADR 019.

**`AgentEvaluator`** (EE stub in OSS)

Multi-turn reasoning evaluator. Uses an agent with tool access to evaluate complex
responses. Config supports whitelisted runtime overrides: `model`, `agent_mode`,
`check_internet`, `knowledge_base_id`, `tools`, etc.

**`LlmEvaluator`, `Groundedness`** (EE stubs in OSS)

EE-only evaluators. OSS builds get stub classes that raise `FeatureUnavailable`.

### Function-Based

**`FunctionEvaluator`** — wraps 92 deterministic functions from `functions.py`.

Constructor: `FunctionEvaluator(function_name, function_arguments=None)`

`_evaluate()`: looks up function in `operations` dict by `function_name`, calls
`operator(**inputs, **function_arguments)`. Functions return `{"result": bool|float, "reason": str}`.

**All 92 functions follow this contract:** `(reference, actual, **kwargs) → {"result": bool|float, "reason": str}`

**`CustomCodeEval`** — runs arbitrary Python in a sandboxed subprocess.

**`ApiCall`** — calls an external HTTP endpoint with the eval inputs.

### Grounded

**`GroundedEvaluator`** — computes string similarity via a configurable comparator.

Comparators: `JaccardSimilarity`, `NormalisedLevenshteinSimilarity`, `JaroWincklerSimilarity`,
`SorensenDiceSimilarity`, `CosineSimilarity`.

Failure condition: `score < failure_threshold` (default 0.5).

### FutureAGI Internal

**`DeterministicEvaluator`** (EE) — constraint-based, multimodal-capable. Uses Turing models.
**`RankingEvaluator`** (EE) — preference learning. Uses Turing models.
**`FormalConstraintEvaluator`** — Z3 SMT solver. See `evaluations/SPEC.md` for contract.

---

## LLM Provider Integration (`core_evals/run_prompt/`)

Unified provider gateway for all LLM-calling evaluators.

**`available_models.py`** (386 KB) — comprehensive model registry with pricing metadata.

**Provider routing:**
- Turing models (`turing_large`, `turing_small`, `turing_flash`) → `TuringClient` (internal)
- Protect models (`protect`, `protect_flash`) → `DeterministicEvaluator` shortcut
- External models → LiteLLM with provider-resolved API key

**Cost calculation:** `model_pricing.py` maps `(model, prompt_tokens, completion_tokens)` → cost.
Stored on `eval_instance.cost` and `eval_instance.token_usage` post-run.

---

## Registry Structure

`core_evals/fi_evals/__init__.py` exports `__all__` — a list of ~95 class names.
`evaluations/engine/registry.py` imports this module and registers each class by
`cls.name` on first call (lazy singleton). See evaluations `SPEC.md` for full registry contract.

**Invariant:** Every class in `__all__` must have a `.name` property that returns its
stable `eval_type_id` string. The registry uses `cls.name` — not `cls.__name__` — as
the lookup key.

---

## Batch Execution

**Async / Temporal path** (`model_hub/tasks/user_evaluation.py`):

- `process_single_evaluation()` — Temporal activity for dataset row evaluation
- `process_experiment_evaluation()` — multi-prompt experiment evaluation
- Distributed state tracking via `evaluation_tracker`
- Cancellation via `should_cancel()` callback checked between evals

**Thread-pool path** (`BaseEvaluator.run_batch()`):
- `ThreadPoolExecutor(max_workers=max_parallel_evals)`
- Per-item errors caught → `None` in `eval_results` (never raises from batch)
- Callers must distinguish `None` (eval error) from `EvalResult(failure=True)` (eval ran, failed)

---

## Known Design Issues

- **`CustomPromptEvaluator` violates the evaluator protocol** — ADR 019, issue #314
- **`EvalResult.metadata` is always a JSON string, not `str | None`** — ADR 021, issue #315
- **Silent template context truncation** — issue #316
- **`PreserveUndefined` hides prompt typos** — issue #317
- **`required_args` is abstract but FunctionEvaluator returns `[]`** — no enforced validation contract
- **`_log_evaluation_request/results()` swallows all exceptions** — silent Fi logging failures

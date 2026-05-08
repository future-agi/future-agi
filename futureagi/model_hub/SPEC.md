# model_hub — Specification

`model_hub` is the **LLM evaluation and prompt engineering platform**. It owns the
dataset schema (Row/Column/Cell), the eval execution orchestration layer
(`EvaluationRunner`), prompt template management, LiteLLM-based LLM routing, and
the async task pipeline that drives everything at scale.

```
User action (API / SDK)
    → UserEvalMetric created (binds EvalTemplate to Dataset)
    → Celery/Temporal task: process_single_evaluation()
    → EvaluationRunner(user_eval_metric_id)
        ├─ load_user_eval_metric() — resolve template, mappings, dataset
        ├─ _run_evaluation(row, mappings) — map columns → params → run_eval()
        └─ _create_cell() — write result to Cell
```

---

## Core Data Models

### Dataset

Container for tabular evaluation data.

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | |
| `source` | enum | `BUILD / DEMO / OBSERVE` |
| `column_order` | ArrayField(UUID) | Ordered display sequence |
| `eval_reasons`, `eval_reason_status` | JSONField | Structured eval summary |
| `dataset_config`, `synthetic_dataset_config` | JSONField | Metadata |

### Column

One column in a dataset. Tracks provenance and execution status.

| Field | Type | Notes |
|-------|------|-------|
| `data_type` | enum | `TEXT / BOOLEAN / INTEGER / FLOAT / JSON / ARRAY / IMAGE / AUDIO / DOCUMENT / …` |
| `source` | enum | `EVALUATION / RUN_PROMPT / EXPERIMENT / OPTIMISATION / …` |
| `source_id` | UUID | FK to the object that owns this column (eval ID, run_prompt ID, etc.) |
| `status` | enum | `RUNNING / COMPLETED / FAILED` |
| `metadata` | JSONField | e.g. `{node_id}` for agent playground nodes |

### Row

One row in a dataset. Ordered within a dataset.

| Field | Notes |
|-------|-------|
| `order` | int — display position; indexed with `(dataset, order)` |

### Cell

One data point at `(row, column)` intersection.

| Field | Type | Notes |
|-------|------|-------|
| `value` | TextField | Result text |
| `value_infos` | JSONField | `{reason, error_code, …}` |
| `status` | enum | `PASS / ERROR / RUNNING / …` |
| `feedback_info` | JSONField | `{user_id, verified, label_id}` annotation metadata |
| `prompt_tokens`, `completion_tokens`, `response_time` | numeric | Per-cell cost |

**Index:** GIN trigram on `value` where `deleted=False AND status=PASS` — full-text search.

### EvalTemplate

Reusable evaluation definition. Config determines evaluator class and parameters.

| Field | Notes |
|-------|-------|
| `config` | JSONField — must contain `eval_type_id`; also carries `output`, `required_keys`, `param_modalities`, etc. |
| `criteria` | str — injected into FutureAGI evals as the scoring rubric |
| `eval_type` | enum — `llm / code / agent` (coarse category, not the registry key) |
| `choice_scores` | JSONField — maps choice strings to float scores |

### UserEvalMetric

One eval run: binds an `EvalTemplate` to a `Dataset` with column mappings.

| Field | Notes |
|-------|-------|
| `template` | FK → `EvalTemplate` |
| `dataset` | FK → `Dataset` |
| `config` | JSONField — `{mapping: {param_name: column_id}, config: {…run overrides…}}` |
| `status` | enum — `RUNNING / COMPLETED / STOPPED / ERROR` |
| `experiment_dataset` | FK, nullable — set for experiment-mode evals |

### RunPrompter

One prompt execution job: template + dataset + concurrency config.

| Field | Notes |
|-------|-------|
| `messages` | ArrayField of `{role, content}` dicts with `{{placeholder}}` syntax |
| `model` | str |
| `concurrency` | int 1–10 (default 5) |
| `output_format` | enum — `array / string / number / object / audio / image` |
| `response_format` | JSONField — JSON schema for structured outputs |
| `tools` | M2M — OpenAI-style function calling tools |
| `status` | enum |

---

## EvaluationRunner

`model_hub/views/eval_runner.py` — orchestrates single-row eval execution against a
dataset. Not a Django view class — instantiated directly by async tasks.

### Constructor

```python
EvaluationRunner(
    user_eval_metric_id,
    experiment_dataset=None,    # Set for experiment-mode evals
    column=None,                # Pre-existing result column (skip creation)
    optimize=None,              # Optimisation job reference
    is_only_eval=False,         # True = eval-only, no prompt run
    format_output=False,
    cancel_event=None,
    futureagi_eval=False,       # True = FutureAGI internal model path
    protect=False,              # True = protect model path
    protect_flash=False,
    source=None, source_id=None, source_configs=None,
    sdk_uuid=None,
    organization_id=None,
    workspace_id=None,
    version_number=None,        # Pinned EvalTemplateVersion
)
```

### Key methods

**`load_user_eval_metric()`** — loads `UserEvalMetric`, resolves `EvalTemplate`,
`Dataset`, column mappings from `config["mapping"]`. Sets status → `RUNNING`.

**`_run_evaluation(row, mappings, config) → (response, status, value)`**

The core eval logic:
1. `_prepare_mapping_data(row, mappings)` — resolves each `{param_name: column_id}` pair:
   - Looks up `Cell(row=row, column=column_id)` → `cell.value`
   - Handles JSON path extraction: `"column_id.nested.path"` → `json_loads(cell.value)["nested"]["path"]`
   - Special values: `"output"` → prompt output cell, `"prompt_chain"` → formatted message history
2. Validates required/optional keys and modality constraints.
3. Injects ground-truth config and data injection if configured.
4. Calls `run_eval(EvalRequest(...))` from `evaluations/engine/runner`.
5. Returns `(raw_response_dict, cell_status, formatted_value)`.

**`run_evaluation_for_row(row_id)`** — single-row pipeline:
calls `_run_evaluation()` then `_create_cell()`.

**`_create_cell(dataset, column, row, response, value, status)`** — writes result
to `Cell`. Uses `bulk_update_or_create_cells()`.

**Stop guard** (checked before every write): if `is_user_eval_stopped(user_eval_metric_id)`
returns `True`, the method returns `(0, 0)` without writing. Prevents late Temporal
workers from overwriting user-initiated stops.

**`_check_and_update_eval_status(column_id)`** — after each cell write, checks if
all cells in the column are `PASS` or `ERROR`. If so:
- Sets `UserEvalMetric.status = COMPLETED`
- Sets `Column.status = COMPLETED`
- Triggers `check_and_update_experiment_dataset_status()` cascade if in experiment mode

### Column mapping resolution

`config["mapping"]` maps `param_name → column_id_or_special_value`.

| Value | Resolves to |
|-------|-------------|
| `"<uuid>"` | `Cell.value` for that column in the current row |
| `"<uuid>.path.to.field"` | JSON path into `Cell.value` |
| `"output"` | The prompt run output cell for this row |
| `"prompt_chain"` | Formatted message history string |

Missing required keys → cell written with `status=ERROR`. Missing optional keys →
eval runs without that param.

---

## LiteLLM Routing

### Provider resolution

`LiteLLMModelManager` (`agentic_eval/core_evals/run_prompt/litellm_models.py`):

1. Load `AVAILABLE_MODELS` (100+ provider/model combos from `available_models.py`).
2. Append custom models from `CustomAIModel` table for the org.
3. Filter deprecated models.
4. On a prompt run: resolve API key via `ApiKey` table —
   `(organization, provider, workspace?)` → encrypted key or JSON config.

**Lookup priority:** workspace-scoped key → org-default key.

**Known fragility:** if multiple `ApiKey` rows match the same `(org, provider)`,
`.first()` is used — undefined ordering picks one silently. See ADR 024.

### API key types

| Provider | Key format |
|----------|-----------|
| Standard (OpenAI, Anthropic, …) | `actual_key` (encrypted string) |
| Vertex AI, Azure, Bedrock, SageMaker | `actual_json` (encrypted JSON config) |

### LiteLLM call

`RunPrompt.litellm_response()` calls `litellm.completion()` with:
- `messages`, `model`, `temperature`, `max_tokens`, `top_p`
- `response_format` (JSON schema if structured output)
- `tools`, `tool_choice` (function calling)
- Provider-specific headers from `actual_json`

---

## Prompt Execution

### Placeholder substitution (`populate_placeholders()`)

Input: `messages` (list of `{role, content}`), `dataset_id`, `row_id`, `col_id`

Syntax: `{{column_name}}`, `{{column_uuid}}`, `{{column_uuid.nested.path}}`,
`{{column[0]}}` (array indexing)

1. Find all `{{...}}` tokens in message content.
2. For each: look up `Cell(row, column)` → value.
3. Substitute. Media (images, audio): encode as base64 inline or attach as block.

**No validation** that all placeholders were filled — unresolved tokens remain as
literal `{{...}}` text in the message sent to the LLM. See issue #319.

### Concurrency

`RunPrompter.concurrency` (1–10, default 5) controls `ThreadPoolExecutor` size.
Rows are distributed across threads; results written back to Cells.

---

## Async Task Pipeline

### `process_single_evaluation()` (Temporal activity / Celery task)

1. Acquire distributed lock (Redis) — prevents duplicate runs for same `user_eval_metric_id`.
2. Pre-check usage credits via `log_and_deduct_cost_for_api_request()`.
3. Guard: check `is_user_eval_stopped()`.
4. Create result `Column(source=EVALUATION)` — `select_for_update()` prevents double-creation.
5. Instantiate `EvaluationRunner` and call `run_evaluation_for_row()` per row, in parallel.
6. On completion: `_check_and_update_eval_status()`.

### `process_prompts_single()` (Celery)

Executes `RunPrompter` jobs. ThreadPoolExecutor with `concurrency` workers.
Per row: `populate_placeholders()` → `litellm_response()` → write `Cell`.

### Experiment cascade

When an eval column completes inside an experiment:
`check_and_update_experiment_dataset_status()` checks whether all evals for
that experiment dataset template are done, then marks the EDC (experiment dataset
column) complete. Multiple parallel completions can trigger this simultaneously.
See ADR 023.

---

## Known Design Issues

- **`MultipleObjectsReturned` on API key lookup silently uses `.first()`** — ADR 022, issue #319
- **Prompt placeholder substitution has no unresolved-token validation** — ADR 024, issue #321
- **Experiment status cascade can thrash under parallel completion** — ADR 023, issue #320
- **JSON path resolution fails silently** — falls back to raw value, no error surfaced
- **Cell.value is an untyped TextField** — typed columns (INTEGER, FLOAT, JSON) store as string; callers parse at read time with no schema enforcement
- **Stop guard has a late-write window** — a worker that passed the guard but hasn't written yet can still write after the user stops

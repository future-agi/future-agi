# Evaluations Engine — Specification

The evaluations engine is a **stateless execution layer**. It receives a request, looks up the
right evaluator, instantiates it, runs it, and returns a formatted result. It owns no persistence;
all Django ORM operations are delegated to callers or resolved via injected models.

---

## Entry Point: `run_eval(request: EvalRequest) -> EvalResult`

`runner.py` — the single function all eval execution paths call.

### Contract

| Input | Constraint |
|-------|-----------|
| `request.eval_template` | Must have `.config["eval_type_id"]`, `.config["output"]`, `.criteria`, `.name` |
| `request.inputs` | Dict of pre-resolved key→value pairs |
| `request.model` | Optional override; falls through to template default |
| `request.organization_id` | Required for API key resolution in LLM evals and few-shot RAG |

| Output | Guarantee |
|--------|-----------|
| `result.value` | Formatted for the template's `output_type` (see Formatting) |
| `result.failure` | Human-readable error string if the eval failed; `None` on success |
| `result.duration` | Always set; `end_time - start_time` (wall clock, includes network) |
| `result.cost` | `dict` from `eval_instance.cost` if the evaluator tracks it; `None` otherwise |
| `result.token_usage` | `dict` from `eval_instance.token_usage` if tracked; `None` otherwise |

### Invariants

- **Raises `ValueError`** if `eval_type_id` is missing from `eval_template.config` or the
  evaluator class is not in the registry. All other errors surface as `result.failure`.
- **Protect model shortcut**: if `request.inputs["call_type"]` is `"protect"` or
  `"protect_flash"`, `eval_type_id` is overridden to `"DeterministicEvaluator"` and
  `request.model` is set to `"protect"` / `"protect_flash"` respectively. This is runtime
  context, not a template property — the same template can be called either way.
- **`skip_params_preparation`**: when `True`, `inputs` is passed directly to the evaluator
  without `prepare_run_params`. For programmatic callers that pre-build params.
- **`result.cost = None`** means the evaluator has no billing data. Callers must guard before
  emitting billing events.

---

## Registry — `registry.py`

### Contract

- `get_eval_class(eval_type_id)` → evaluator class registered under that name.
  - Raises `ValueError` if not registered.
- `is_registered(eval_type_id)` → `bool`, never raises.
- `list_registered()` → list of all registered names in `__all__` category order
  (base → LLM → string/regex → scoring/RAG → image/media). Not alphabetical.

### Invariants

- Built **lazily** on first call; cached globally via `_BUILT` flag. Re-importing is a no-op.
- Every name in `agentic_eval.core_evals.fi_evals.__all__` is registered exactly once.
- If `fi_evals` fails to import, `_build_registry` re-raises — the registry never silently
  returns an empty set.

---

## Instance Creation — `instance.py`

### `create_eval_instance(...) → (evaluator_instance, criteria_str)`

Returns a `(evaluator, criteria)` tuple. The `criteria` string is needed by `prepare_run_params`
and is extracted from config during instance creation so the runner doesn't need to re-parse it.

Steps:

1. **`resolve_version`** — fetches `EvalTemplateVersion` and increments `usage_count`. Returns
   `None` if no version system is configured (OSS mode — the exception is swallowed).
2. **`prepare_eval_config`** — routes to a type-specific config builder:
   - `CustomCodeEval` → `{"code": <python_source>}` only.
   - `CustomPromptEvaluator` → `{rule_prompt, system_prompt, output_type, model, provider, ...}`.
     API key resolved via `LiteLLMModelManager`.
   - `AgentEvaluator` → full agent config with `rule_prompt`, `model`, `tools`, `data_injection`, etc.
   - FutureAGI evals (`RankingEvaluator`, `DeterministicEvaluator`) → `model` and `provider`
     resolved via `_get_futureagi_model_config` → `ModelConfigs`. `criteria` is extracted
     from config and returned as the second element.
3. **`apply_version_overrides`** — merges `resolved_version.prompt_messages` into config.
   Version criteria fills in only if `criteria` is currently `None`.
4. **AgentEvaluator runtime overrides** — whitelisted keys from `runtime_config["run_config"]`
   overwrite config after version overrides (model, agent_mode, tools, etc.).
5. **Instantiate** with unpacked config.

### Invariants

- `organization_id`, `workspace_id`, `user_id` are stripped from config for all non-`AgentEvaluator` types.
- The caller (`run_eval`) passes `config_overrides` as the initial `config` dict; type-specific
  logic builds on top of it, so caller values form the baseline.
- An unknown model string does not raise here; it propagates to the evaluator.

---

## Param Preparation — `params.py`

### `prepare_run_params(inputs, eval_template, is_futureagi, criteria, organization_id, workspace_id) → dict`

**For FutureAGI evals only** (`is_futureagi=True`):

| Key injected | Source |
|---|---|
| `few_shots` | RAG retrieval from `EmbeddingManager`; `[]` if no `organization_id` or retrieval fails |
| `criteria` | `criteria` arg → `eval_template.criteria` (only if `"criteria"` not already in inputs) |
| `eval_name` | `eval_template.name` |
| `required_keys` | `eval_template.config.get("required_keys", [])` |
| `param_modalities` | `eval_template.config["param_modalities"]` (only if key present in config) |
| `config_params_desc` | `eval_template.config["config_params_desc"]` (only if key present in config) |

**For `CustomPromptEvaluator` and `AgentEvaluator`**:

| Key injected | Source |
|---|---|
| `required_keys` | `eval_template.config.get("required_keys", [])` (only if not already in inputs) |

### Invariants

- Non-FutureAGI evals (CustomPromptEvaluator, AgentEvaluator, CustomCodeEval, etc.) do **not**
  get `few_shots` — few-shot RAG is a FutureAGI eval feature because only Turing models are
  trained to use the `few_shots` kwarg.
- `EmbeddingManager` failure or missing `organization_id` → `few_shots = []`, never raises.

---

## Preprocessing — `preprocessing.py`

Transforms inputs **before** the evaluator runs. Registered via `@register_preprocessor(name)`.

| Registered key | What it adds |
|---|---|
| `"ClipScore"` | `_image_embeddings`, `_text_embeddings` (from image URLs and text strings) |
| `"FidScore"` | `_fid_precomputed_score` (Inception v3 FID computed directly); `_real_features`, `_fake_features` (placeholder arrays — FID already computed) |

**Lookup key is `eval_type_id`** (from `eval_template.config["eval_type_id"]`), not the
template's human-readable `name`. This ensures preprocessing is stable across template renames.

### Invariants

- `preprocess_inputs(name, inputs)` is a no-op if no preprocessor is registered for that name.
- Preprocessing failures log a warning and return inputs unmodified — the evaluator then runs
  without the pre-computed values and will produce its own error.

---

## Formatting — `formatting.py`

### `format_eval_value(result_data, eval_template) → Any`

| `output_type` | Output |
|---|---|
| `"Pass/Fail"` | `"Passed"` / `"Failed"` (str) for most evals. For `DeterministicEvaluator`: `data[0]` (single-choice) or `data` (multi-choice) — because Deterministic returns the label directly, not a boolean |
| `"score"` | `float` from `metrics[0]["value"]`; `None` if metrics list is empty |
| `"numeric"` | `float` from `metrics[0]["value"]`; `None` if metrics list is empty |
| `"reason"` | `str` from `result_data["reason"]` |
| `"choices"` | `{"score": float, "choice": str}` (single string result) or `{"score": float, "choices": list}` (list result) |
| unknown | `None` — intentional sentinel; callers treat `None` as "no result", not as an error |

If `eval_template.choice_scores` is non-empty and `output_type` is not `"Pass/Fail"`,
output_type is **forced** to `"choices"` regardless of the template setting.

### `extract_raw_result(eval_result, eval_template) → dict`

Reads `eval_template.config.get("output", "score")` — from the config dict, not a top-level
attribute — and normalises the first `eval_result.eval_results[0]` into the standard response dict.

### Invariants

- For `"choices"`: unmapped choice string → score defaults to `0.0`.
- `None` for unknown output type is intentional (matches `format_output()` in the legacy
  `EvaluationRunner`). Callers treat it as "indeterminate" rather than raising.

---

## OSS Stubs — `tfc/oss_stubs/usage.py`

Provides no-op implementations of EE billing symbols so OSS installs don't crash on import.

### Contract

- `log_and_deduct_cost_for_resource_request(...)` → `_NullCallLog`
  - `.status == APICallStatusChoices.SUCCESS.value` — passes all existing resource-limit guards
  - `.save()` is a no-op
- `log_and_deduct_cost_for_api_request(...)` → same
- `APICallTypeChoices`, `APICallStatusChoices` — `str` enums, all values referenced in OSS code
- `ROW_LIMIT_REACHED_MESSAGE` — non-empty string (quota enforcement disabled, but message exists)
- `refund_cost_for_api_call`, `count_text_tokens`, `count_tiktoken_tokens` — callable no-ops

### Invariants

- `_NullCallLog.status != APICallStatusChoices.RESOURCE_LIMIT.value` — no resource-limit guard
  ever fires on OSS.
- Import-safe: no side effects, no Django setup required.

---

## `FormalConstraintEvaluator`

### Contract

- Inputs: `task_description` (str), `agent_response` (JSON str or dict), `constraints` (JSON str or dict)
- `constraints` schema: `{"variables": {name: {type, min?, max?}}, "constraints": [{type, vars, value?}]}`
- Output metrics: `passed` (0.0 or 1.0), `formal_correctness` (0.0 or 1.0)
- `model` field in result: `"z3-solver"`

### Invariants

- Invalid JSON in `agent_response` → `failure=True`, reason explains parse error.
- Unsatisfiable constraint spec → `failure=True`, reason identifies the spec as broken.
- Assignment violates constraints → `failure=True`, reason names each violated constraint by index.
- Assignment satisfies all constraints → `failure=False`, reason includes Z3 certificate.
- `z3` not installed → `failure=True` with install instruction.
- Variable domain bounds (`min`, `max`) are enforced as constraints; out-of-domain values fail.

# Architecture Decision Records

Design decisions, known fragilities, and non-obvious choices across the Future AGI platform.
Each ADR has: Status · Evidence (file:line) · Context · Decision · Why · Consequences.

## evaluations/engine

| # | Title | Status |
|---|-------|--------|
| [001](001-eval-runner-is-not-a-view.md) | `EvaluationRunner` is not a Django view | Intentional |
| [002](002-stop-guard-late-write-window.md) | Stop guard has a late-write window | Known limitation |
| [003](003-cell-value-untyped-text-field.md) | `Cell.value` is an untyped TextField | Known limitation |
| [004](004-preprocessing-keyed-on-eval-type-id.md) | Preprocessing dispatch keyed on `eval_type_id` | Fixed — issue #301 |
| [005](005-json-path-resolution-silent-fallback.md) | JSON path resolution falls back silently | Known limitation |
| [006](006-column-mapping-special-values.md) | Column mapping special values (`output`, `prompt_chain`) | Intentional |
| [007](007-experiment-cascade-parallel-completion.md) | Experiment status cascade under parallel completion | Known limitation |
| [008](008-eval-template-version-pinning.md) | EvalTemplate version pinning via `version_number` | Intentional |

## tracer

| # | Title | Status |
|---|-------|--------|
| [009](009-ingest-two-phase-otlp.md) | Two-phase OTLP ingest (HTTP → Temporal activity) | Intentional |
| [010](010-clickhouse-async-write-through.md) | ClickHouse async write-through via background thread | Known limitation |
| [011](011-span-graph-in-postgres.md) | Span relationship graph stored in PostgreSQL, not ClickHouse | Intentional |
| [012](012-trace-scanner-inside-atomic.md) | `_trigger_trace_scanner` fires inside `transaction.atomic()` | Intentional |
| [013](013-ingest-redis-staging-before-temporal.md) | Redis payload staging before Temporal activity | Known limitation |

## simulate

| # | Title | Status |
|---|-------|--------|
| [014](014-temporal-workflow-per-test-execution.md) | One Temporal workflow per `TestExecution` | Intentional |
| [015](015-persona-first-element-only.md) | Persona: only first list element used | Problematic — issue #309 |
| [016](016-call-metadata-untyped-dict.md) | `call_metadata` is an untyped dict | Known limitation |
| [017](017-chat-sim-initiate-chat-contract.md) | `ChatSimService.initiate_chat()` contract | Intentional |
| [018](018-unresolved-template-variables-reach-llm.md) | Unresolved template variables reach the LLM silently | Problematic — issue #312 |

## agentic_eval

| # | Title | Status |
|---|-------|--------|
| [019](019-custom-prompt-evaluator-inherits-llm-not-base.md) | `CustomPromptEvaluator` inherits `LLM`, not `BaseEvaluator` | Problematic — issue #314 |
| [020](020-batch-run-result-none-on-error.md) | `run_batch()` returns `None` entries for per-item errors | Known limitation |
| [021](021-eval-result-metadata-is-json-string.md) | `EvalResult.metadata` is always a JSON string | Intentional |

## model_hub

| # | Title | Status |
|---|-------|--------|
| [022](022-api-key-first-silent-ambiguity.md) | `ApiKey` lookup falls back to `.first()` on ambiguous match | Problematic — issue #319 |
| [023](023-experiment-status-cascade-optimistic-lock.md) | Experiment status cascade uses optimistic locking | Known limitation — issue #320 |
| [024](024-populate-placeholders-no-unresolved-validation.md) | `populate_placeholders` passes unresolved tokens silently | Problematic — issue #321 |

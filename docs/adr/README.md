# Architecture Decision Records

Each ADR documents a non-obvious design decision: why it was made, what the alternatives
were, and what the consequences are.

## Evaluations Engine (001–008)

| # | Title |
|---|-------|
| [001](001-evaluations-engine-extraction.md) | Why the engine was extracted from `EvaluationRunner` |
| [002](002-few-shots-futureagi-only.md) | Why few-shot RAG is FutureAGI-eval-only |
| [003](003-result-failure-string.md) | Why `EvalResult.failure` is a string, not a bool |
| [004](004-cost-none-not-empty-dict.md) | Why `cost`/`token_usage` are `None` when absent |
| [005](005-registry-lazy-singleton.md) | Why the evaluator registry is a lazy singleton |
| [006](006-protect-shortcut-in-runner.md) | Why the protect shortcut reads runtime inputs |
| [007](007-preprocessing-keyed-on-template-name.md) | Why preprocessing was keyed on template name (fragility → #301, fixed in #304) |
| [008](008-oss-stubs-not-none-fallbacks.md) | Why OSS billing stubs replace `None` fallbacks |

## Tracer App (009–013)

| # | Title |
|---|-------|
| [009](009-eval-status-denormalized-on-span.md) | Why `eval_status` on `ObservationSpan` is stale (→ #305) |
| [010](010-root-span-input-output-last-writer-wins.md) | Why root span `input`/`output` is last-writer-wins |
| [011](011-scanner-unconditional-10s-delay.md) | Why the scanner waits 10s unconditionally |
| [012](012-clickhouse-consistency-window-undefined.md) | Why ClickHouse dual-write has no consistency SLA |
| [013](013-ingest-redis-staging-before-temporal.md) | Why OTLP payloads stage in Redis before Temporal |

## Simulate App (014–018)

| # | Title |
|---|-------|
| [014](014-temporal-vs-celery-dual-execution-path.md) | Why the Celery fallback was kept alongside Temporal (→ #310) |
| [015](015-agent-version-snapshot-as-config-source.md) | Why `configuration_snapshot` is the source of truth for call config (→ #309) |
| [016](016-persona-attributes-as-json-arrays.md) | Why persona attributes are JSON arrays but only first element is used (→ #311) |
| [017](017-chat-session-id-backward-compat.md) | Why `vapi_chat_session_id` / `chat_session_id` fallback exists |
| [018](018-temporal-continue-as-new-at-500-events.md) | Why `TestExecutionWorkflow` uses continue-as-new at 500 events |

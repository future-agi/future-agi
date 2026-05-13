# Backend Style Guide

Extracted from reading across `evaluations/`, `tracer/`, `simulate/`, `agentic_eval/`, and `model_hub/`. These patterns reflect what is actually in the codebase, not aspirational rules.

---

## Logging

**Preferred:** structlog keyword-argument form — event name as a string, context as keyword args.

```python
logger.info(
    "eval_executed",
    template=eval_template.name,
    eval_type=eval_type_id,
    duration=round(response["duration"], 3),
)

logger.error(
    "trace_payload_not_found_in_redis",
    payload_key=payload_key,
)
```

**Avoid:** f-string log messages. They defeat structlog's structured output and make log aggregation harder.

```python
# Don't do this
logger.warning(f"End user not found for key: {end_user_key}. Skipping link.")
```

**Event name convention:** `snake_case_verb_noun` — `eval_executed`, `trace_payload_retrieved`, `api_key_multiple_rows_for_provider`. Make it grep-friendly. Avoid generic names like `"error"` or `"warning"`.

**Logger initialization:** always at module level, never inside functions.

```python
logger = structlog.get_logger(__name__)
```

---

## Error handling

**Explicit exception types** — catch the specific exception, not `Exception`, unless you're writing a catch-all fallback (and log it when you do).

```python
# Good
try:
    api_key_entry = ApiKey.objects.get(**query)
except ApiKey.DoesNotExist:
    raise ValueError(f"API key not configured for {provider}.")
except ApiKey.MultipleObjectsReturned:
    logger.warning("api_key_multiple_rows_for_provider", provider=provider)
    api_key_entry = ApiKey.objects.filter(**query).order_by("-created_at").first()
```

**Fail loud at boundaries, fail open internally** — validation errors at API boundaries raise (DRF returns 400). Internal processing errors (e.g., a missing cell in a batch) are logged and skipped, not raised, to keep the batch alive.

**Never silently swallow non-media exceptions and return stale data.** If you must fall back, log at WARNING with the tokens/keys that were unresolved.

---

## Django ORM patterns

**Prevent double-creation with `select_for_update()`** inside `transaction.atomic()`:

```python
with transaction.atomic():
    dataset_obj = Dataset.objects.select_for_update().get(id=dataset_id)
    column, created = Column.objects.get_or_create(**column_config)
```

**Bulk operations for multi-row writes:**

```python
Cell.objects.bulk_create(cells_to_create)
Cell.objects.bulk_update(cells_to_update, update_fields)
```

**Optimistic locking for concurrent status updates** (avoids `select_for_update` holding locks across long tasks):

```python
updated = Model.objects.filter(id=obj_id, status=current_status).update(status=new_status)
if updated == 0:
    # Another worker already updated — skip cascade
    return
```

**Custom manager for workspace-scoped queries:** use `Model.no_workspace_objects` when querying admin objects that don't have a workspace. `Model.objects` includes workspace filters by default in multi-tenant models.

**Ordering matters on `.first()`:** never call `.filter(...).first()` without an `ORDER BY` when determinism matters. Prefer `.order_by("-created_at").first()` or `.order_by("order").first()` depending on context.

---

## Async task patterns

**Celery tasks** are in `tasks.py` per app. **Temporal activities** use the `@temporal_activity` decorator from `tfc.temporal`.

**Stop guard before every write** — check `is_user_eval_stopped()` or equivalent before writing results. Workers can arrive late after a user-initiated stop.

**Distributed lock** at task entry using Redis to prevent duplicate runs for the same job ID.

**Dead-letter pattern for Redis staging:** OTLP payloads are stored in Redis with 24h TTL before Temporal retrieves them. If Redis returns None (TTL expired), log at ERROR and raise — do not silently skip.

---

## Pure functions and testability

**Extract pure functions** from Django-coupled modules into their own files when possible. Pure functions (no DB, no network, no side effects) can be verified with Z3 + Hypothesis without Docker.

**Decision tree invariants** are good Z3 candidates: eval type dispatch, output format selection, status transition logic.

**Do not stub `__init__.py` chains** — if a module's package `__init__.py` imports heavy deps, use `importlib.util.spec_from_file_location` to load the target file directly rather than fighting the import graph.

---

## Naming conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Structlog event names | `snake_case_verb_noun` | `eval_executed`, `trace_ingested` |
| Celery task functions | `process_<noun>_<verb>()` | `process_single_evaluation()` |
| Temporal activities | decorated with `@temporal_activity` | |
| Django model source enum | `SNAKE_CASE` | `EVALUATION`, `RUN_PROMPT`, `OBSERVE` |
| Status enum | `RUNNING / COMPLETED / FAILED / ERROR` | |
| Config JSONField keys | `camelCase` | `mapping`, `eval_type_id`, `output` |
| ADR files | `NNN-kebab-case-title.md` | `022-api-key-first-silent-ambiguity.md` |

---

## What to avoid

- **f-strings in log calls** — use structlog keyword args instead
- **`.first()` without ORDER BY** on business-critical queries — non-deterministic under Postgres vacuum
- **Catch-all `except Exception` without logging** — makes silent failures invisible
- **Global mutable state in Celery tasks** — tasks share a process; use local variables
- **Importing Django models at module level in utility files** — cascades make those modules untestable without Django setup; lazy-import (`from X import Y` inside the function) or extract pure logic
- **`sys.modules.setdefault()` for test stubs** — use direct assignment (`sys.modules[key] = stub`) so stubs always win over partially-imported real modules
- **Bare `except:` clauses** — always name the exception type
- **`update_fields` omitted on `.save()`** — always specify `update_fields` when updating a single field to prevent overwriting concurrent changes

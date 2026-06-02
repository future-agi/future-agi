---
id: ADR-031
title: Make EndUser.user_id_type non-nullable to fix dedup uniqueness (closes #305)
status: accepted
date: 2026-05-08
related_issues: ["#305"]
---

## Context

`EndUser` has a `unique_together = ("project", "organization", "user_id", "user_id_type")`
constraint. `user_id_type` was nullable (`null=True`), and SQL defines `NULL != NULL`,
so multiple rows with the same `(project, organization, user_id)` but `user_id_type = NULL`
can coexist — the unique constraint never fires. This caused duplicate `EndUser` rows that
inflated unique-user counts in dashboards and produced ambiguous join results (issue #305).

Three options were evaluated:

1. **Make `user_id_type` non-nullable with `default="custom"`** — backfill existing NULLs,
   alter the column, normalise `None` inputs at the application layer.
2. **Partial unique index** — `UNIQUE (project, organization, user_id) WHERE user_id_type IS NULL`.
3. **Application-layer enforcement** — validate before `_fetch_or_create_end_users`.

## Decision

Use **option 1**: make `user_id_type` non-nullable with `default="custom"`.

Reasons:
- The unique constraint already expresses the correct intent; removing `NULL` makes it
  effective without adding a second partial index.
- `"custom"` is the natural semantic default for callers that don't know the ID type:
  it matches the existing `langfuse_upsert.py` which already hardcodes `user_id_type="custom"`.
- The migration backfills existing NULLs before altering the column, so no data is lost
  and no constraint violation occurs during the migration itself.
- Option 2 requires a separate partial index (extra schema surface) and still allows
  NULL rows indefinitely. Option 3 relies on callers being correct — brittle.

## Implementation

1. **Model** (`tracer/models/observation_span.py`) — removed `null=True, blank=True`,
   added `default=UserIdType.CUSTOM` to `user_id_type`.

2. **Migration** (`0074_enduser_user_id_type_non_nullable.py`) — two operations:
   - `RunSQL`: `UPDATE tracer_enduser SET user_id_type = 'custom' WHERE user_id_type IS NULL`
   - `AlterField`: drops nullable, sets default.

3. **Ingestion helpers** (`trace_ingestion.py`, `create_otel_span.py`) — added
   `_norm_uid_type(raw) → str` which returns `raw or "custom"`. Applied at every
   call site that reads `user_id_type` from external span data.

## Consequences

- `EndUser` rows are now guaranteed to have a non-empty `user_id_type`.
- The unique constraint correctly prevents duplicate rows for the same external user.
- Existing code that passes `user_id_type=None` continues to work; it is silently
  normalised to `"custom"` before the DB call.
- The `_norm_uid_type` helper is idempotent and total (proved in Z3/Hypothesis).

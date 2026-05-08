---
status: Problematic — filed as issue #320
date: 2026-05-08
---

# ADR 022 — `ApiKey` lookup falls back to `.first()` on ambiguous match

## Evidence

`futureagi/agentic_eval/core_evals/run_prompt/litellm_models.py:167-169`:
```python
except ApiKey.MultipleObjectsReturned:
    # Fallback to first match if multiple keys exist (e.g., workspace not specified)
    api_key_entry = ApiKey.objects.filter(**query).first()
```

The fallback `.filter(...).first()` has no `ORDER BY` clause. Django's default ordering
for `ApiKey` is not guaranteed to be deterministic across PostgreSQL vacuums.

## Context

`ApiKey` rows are keyed on `(organization, provider, workspace?)`. When a workspace is
not specified in the query, multiple rows can match the same `(org, provider)` pair.
The `MultipleObjectsReturned` exception is caught silently and the first row from an
unordered queryset is used.

## Decision

The fallback was chosen over raising an error to avoid breaking existing callers that
had inadvertently created duplicate rows. The `.first()` comment acknowledges the
ambiguity but suppresses it.

## Why

At the time of writing, some organisations had duplicate `ApiKey` rows created through
the admin or via race conditions in the API key creation endpoint. Raising
`MultipleObjectsReturned` to the caller would have broken prompt runs for those orgs.

## Consequences

- Which API key is used for an ambiguous lookup is non-deterministic across PostgreSQL
  query plan changes, vacuum cycles, and row ordering.
- If one of the duplicate keys is expired or invalid, the silent `.first()` can pick
  it, causing intermittent authentication failures with no actionable error message.
- No warning or metric is emitted when the fallback fires — operators have no
  visibility into ambiguous-key lookups.
- Filed as issue #319.

---
status: Problematic — filed as issue #311
date: 2026-05-08
---

# ADR 016 — Persona attributes stored as JSON arrays, only first element used

## Evidence

`futureagi/simulate/models/` — `Persona` model: `gender`, `age_group`, `personality`,
`accent`, etc. are all `JSONField` storing lists.
`futureagi/simulate/services/` — `Persona.to_voice_mapper_dict()`:
`"gender": persona.gender[0] if persona.gender else "male"`.

## Context

The persona UI allows users to select multiple values per attribute (e.g., two personality
traits, two accent options). Storing as JSON arrays made the model flexible for multi-select
without a schema migration.

## Decision

Attributes are stored as arrays to support multi-select in the UI. At runtime, only
`field[0]` (the first element) is passed to the voice or chat provider.

## Why

The UI multi-select capability was built before the execution layer needed to handle
more than one value per attribute. Combining multiple values (e.g., two accents) into
a single VAPI voice call requires provider-specific logic that was not implemented.

## Consequences

- Users who select multiple values for a persona attribute observe only the first one
  having any effect. The selection appears to be honoured but is silently truncated.
- The data model supports N values; the execution layer supports 1. Any future multi-value
  support requires updating every provider adapter, not the model.
- Filed as issue #311.

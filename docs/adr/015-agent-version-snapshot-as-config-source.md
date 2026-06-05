---
status: Accepted
date: 2026-05-08
---

# ADR 015 — `agent_version.configuration_snapshot` is the source of truth for call config

## Evidence

`futureagi/simulate/temporal/activities/test_execution.py` — `setup_test_execution()`:
loads `agent_version.configuration_snapshot`, not `agent_definition.*` fields.

## Context

An `AgentDefinition` can be edited between when a test is created and when it executes.
If a simulation used the live definition, changes made after the test was queued would
silently alter its config — making it impossible to reproduce a specific test run.

## Decision

`AgentVersion` stores a `configuration_snapshot` (JSONField) capturing the full agent
config at the time the version was created. `setup_test_execution()` uses this snapshot
exclusively. The live `agent_definition` fields are ignored during execution.

## Why

Reproducibility: a test must use the same agent config each time it runs, regardless of
subsequent definition edits. Versioning provides an explicit "this is the config that
was tested" contract.

## Consequences

- If `run_test.agent_version` is `None`, the activity silently falls back to the latest
  `ACTIVE` version. If the agent was modified since the test was created, the fallback
  version may not match the intended config. Filed as issue #309.
- Callers that want deterministic execution must always pin an `agent_version`.
- The snapshot can become stale if the underlying provider API changes what fields mean,
  but the version record holds the old values.

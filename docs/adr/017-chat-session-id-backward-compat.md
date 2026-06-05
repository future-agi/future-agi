---
status: Accepted — cleanup pending
date: 2026-05-08
---

# ADR 017 — `vapi_chat_session_id` / `chat_session_id` backward-compat fallback

## Evidence

`futureagi/simulate/services/chat_sim.py` — `initiate_chat()` and message routing:
`metadata.get("chat_session_id") or metadata.get("vapi_chat_session_id")`

## Context

The chat simulation was originally built exclusively on VAPI. `call_metadata` stored the
session ID under the key `"vapi_chat_session_id"`. When provider-agnostic routing was
added (`ChatServiceManager`), the key was renamed to `"chat_session_id"`.

## Decision

Rather than migrating existing `CallExecution` rows, a fallback was added:
`get("chat_session_id") or get("vapi_chat_session_id")`. New sessions are written under
`"chat_session_id"`.

## Why

A migration of live `CallExecution.call_metadata` JSONField values across all orgs was
risky and unnecessary once the fallback made both old and new records work correctly.

## Consequences

- If a row somehow has both keys set to different values, `or` semantics return the first
  non-falsy value — which is `"chat_session_id"`. The old VAPI key is silently ignored.
  This is the correct behaviour but is not enforced by any invariant.
- `"vapi_chat_session_id"` as a key is a leaked implementation detail visible in all
  `CallExecution.call_metadata` JSON blobs for historical records.
- The old key should be cleaned up once all active records have been migrated.

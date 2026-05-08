---
status: Problematic — filed as issue #319
date: 2026-05-08
---

# ADR 024 — `populate_placeholders` swallows exceptions and silently passes unresolved tokens

## Evidence

`futureagi/model_hub/views/run_prompt.py:460-467`:
```python
    except Exception as e:
        if media_error:
            raise e
        else:
            traceback.print_exc()
            logger.exception(f"Fatal error processing messages: {e}")
            # Return original messages as fallback
            return messages
```

The outer `except` catches all non-media exceptions and returns the original
(unsubstituted) messages. Unresolved `{{column_name}}` tokens reach the LLM verbatim.

Inner per-column loop (`lines 403-407`) also silently `continue`s on column resolution
errors, leaving those tokens unsubstituted without surfacing an error to the caller.

## Context

`populate_placeholders` was built to be resilient to partial dataset states (e.g., rows
where some cells are missing). The original goal was that a missing column should not
abort the entire prompt run.

## Decision

All exceptions outside media handling are caught, logged, and the original messages
returned as fallback. There is no post-substitution scan for remaining `{{...}}` tokens.
The LLM receives whatever text resulted — which may contain unresolved placeholders.

## Why

Early versions crashed entire batch prompt runs when a single cell was missing. The
catch-all fallback was added to keep remaining rows processing. The trade-off (silent
pass-through vs. hard failure) was resolved in favour of resilience.

## Consequences

- LLM receives `{{column_name}}` as literal text when a column is missing or the cell
  is null. The model may interpret these as instructions or simply echo them back,
  producing subtly wrong outputs with no error surfaced to the user.
- There is no way for callers to distinguish "all placeholders resolved" from "fallback
  to original due to exception" — both return a list of messages with exit code 0.
- Debugging placeholder failures requires reading log output; the API response gives no
  indication.
- Parallel to simulate issue #312 (unresolved template variables reach the LLM silently).
- Filed as issue #321.

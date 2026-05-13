# ADR 008 — OSS billing stubs replace `except ImportError: X = None` fallbacks

**Status**: Accepted — implemented in PR [#300](https://github.com/future-agi/future-agi/pull/300), fixes issue [#180](https://github.com/future-agi/future-agi/issues/180)
**Evidence**: issue #180 (symptom report); commit `f9d3529` (stub implementation, 39 files); `tfc/oss_stubs/usage.py`

## Context

The EE (Enterprise Edition) billing system lives in an `ee/` directory that is absent from the
open-source distribution. 39 files across the codebase imported EE symbols with a pattern like:

```python
try:
    from ee.billing import APICallTypeChoices, log_and_deduct_cost_for_api_request
except ImportError:
    APICallTypeChoices = None
    log_and_deduct_cost_for_api_request = None
```

Every call site then used these symbols without guarding against `None`:

```python
call_log = log_and_deduct_cost_for_api_request(...)  # TypeError: NoneType is not callable
if call_log.status == APICallStatusChoices.RESOURCE_LIMIT.value:  # AttributeError: NoneType
```

On a fresh OSS self-host, virtually every core path (dataset creation, eval runs, row adds,
prompt runs) raised `TypeError` or `AttributeError` and returned HTTP 500.

## Decision

Introduce `tfc/oss_stubs/usage.py` with no-op implementations of every EE billing symbol
referenced in OSS code:

- `_NullCallLog` — passes all existing guard checks (`status == SUCCESS`, `save()` is no-op)
- `log_and_deduct_cost_for_resource_request`, `log_and_deduct_cost_for_api_request` — return `_NullCallLog`
- `APICallTypeChoices`, `APICallStatusChoices` — full `str` enums, all referenced values present
- `refund_cost_for_api_call`, `count_text_tokens`, `count_tiktoken_tokens` — callable no-ops
- `ROW_LIMIT_REACHED_MESSAGE` — non-empty string

All 39 `except ImportError: X = None` blocks updated to import from the stub instead.

## Why stubs, not None-guards at call sites

- None-guards scatter billing-awareness throughout the business logic. Every new call site
  would need to remember to guard.
- Stubs require no changes at call sites — they're drop-in replacements that happen to do nothing.
- Stubs can be tested independently to verify they pass all existing guard conditions.
- If EE is present, the real implementation takes precedence via normal import resolution.

## Consequences

- **Never blocks the happy path on OSS**: `_NullCallLog.status` is `SUCCESS`, not `RESOURCE_LIMIT`,
  so no quota gate ever fires.
- **Billing is silently disabled on OSS**: no credits are deducted and no usage events are
  emitted. This is correct for self-hosted OSS but means there is no metering at all.
- **New EE billing symbols** must be added to `oss_stubs/usage.py` when introduced, or OSS
  installs will regress to `AttributeError` on import.

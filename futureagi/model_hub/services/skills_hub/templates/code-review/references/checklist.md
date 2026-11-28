# Review checklist

## Inputs and contracts

Check empty, null, omitted, malformed, maximum, and maximum-plus-one inputs.
Verify serialization and deserialization agree on names, units, time zones, and
optional values. A default must not hide an invalid caller-supplied value.

## Authorization and state

Follow tenant and workspace scope through each lookup, including nested objects.
Check read-only roles, archived objects, stale updates, retries, and concurrent
writers. An idempotent retry must not duplicate an external side effect.

## Failure handling

Identify timeouts, partial success, rollback boundaries, and cancellation.
Security and quota checks fail closed. Optional telemetry may fail open only
with observability. Avoid catching exceptions that hide corrupted state.

## Evidence

Look for tests that fail without the change and assert meaningful content, not
just status codes. Mock boundaries rather than the logic being verified. Note
which tests were run, where, and their actual outcome.

## Finding format

Title: describe the defect, not a proposed refactor.
Location: smallest relevant file and line range.
Scenario: concrete input or interleaving that triggers it.
Impact: what the caller observes or what data is affected.
Evidence: traced path or reproducing test, with uncertainty stated explicitly.

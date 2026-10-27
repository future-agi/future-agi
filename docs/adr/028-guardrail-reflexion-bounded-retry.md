# ADR 028: Bounded guardrail response repair

## Status

Proposed in PR #334 for issue #70. Supersedes the PR's original non-streaming,
gateway-wide retry design.

## Context

An optional repair step can turn a blocked model answer into an acceptable answer
without teaching every client a retry protocol. It must not weaken block
semantics, leak intermediate output, lose provider spend or reset the retry cap
when different checks fail.

The original implementation depended on a missing response as the signal to
retry. That is not a sufficient contract: an explicit response-validation veto
is needed, independently of whether repair is enabled. Local baseline handler
probes showed static and tenant post-block paths producing inconsistent HTTP
outcomes; this is not evidence about a deployed production configuration.

## Decision

- Initialize managed guardrails whenever the gateway has a control-plane URL,
  even when global `guardrails.enabled` is false; the global flag gates only
  operator-defined static rules. Without static rules or a control-plane URL,
  the plugin remains off.
- Use per-rule `config.on_block`, `config.reflect_max_attempts` and
  `config.reflect_debug`, with reject as the default and at most five additional
  generations. Keep the existing extensible wire `config` map.
- Separate attempt admission/accounting, synchronous response validation and
  final-only observers. Propagate a validation veto, but do not turn ordinary
  logging/observer errors into client failures.
- Repair only synchronous post-response content failures. Reject wins when rules
  disagree; the retry limit only tightens across the whole request.
- Build private feedback requests containing the original conversation, rejected
  assistant text and failure feedback. Restore the original request for checks
  and logging. Unsupported blocked tool/reasoning/multi-choice answers reject.
- Scope the initial adapter to `/v1/chat/completions`. Fail closed before the
  provider on other text-generation routes when a reflection rule applies,
  instead of silently falling back to an unbuffered protocol adapter.
- Buffer opted-in streams until the entire candidate passes. Bound retained
  chunks, honor cancellation and send no failed candidate to the client.
- Settle actual observed usage before a rejected response is cleared. Recheck
  limits before additional repair work, preserve ordinary non-reflection
  failover admission behavior and record missing usage honestly.
- Keep final cache/telemetry execution single-shot and rejected content out of
  asynchronous observers and debug history.

## Consequences

Repair increases provider usage and response latency. Buffered streaming trades
immediacy for the ability to validate before exposure. Limits are checked, not
reserved, so the existing concurrent-spend semantics are not strengthened by
this feature. Blocked-call accounting is corrected rather than advertised as
unchanged billing.

Legacy top-level UI stages are not blindly forwarded: absent explicit nested
stage settings, checks retain native behavior. Provider-owned `config.mode` is
not repurposed as an execution mode. No schema migration is required.

## Validation

Use tests against production handlers, guardrails, accounting plugins and the
control-plane/UI boundaries. The separate copied formal models and their CI
workflow are removed; they did not prove the implementation. Existing gateway
CI runs the production suite, with focused race coverage for the changed path.
Actual passing runs and real-user verification remain release requirements.

See [the behavior specification](../specs/guardrail_reflexion.md) for controls,
limits, compatibility and test locations.

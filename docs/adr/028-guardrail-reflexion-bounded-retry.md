# ADR 028: Guardrail reflexion — bounded post-stage retry with feedback injection

## Status

Accepted

## Context

Issue #70: "Guardrails are binary (block/pass) — add response reflexion at the gateway."

When a post-stage guardrail check blocks the model's response (e.g., a PII leak, policy violation, or content-moderation hit), the gateway currently returns a 403 to the client. The model had no opportunity to correct itself — it generated an output it couldn't know would fail.

**Reflexion** is the pattern of injecting the failure reason back into the conversation (as a user-turn message) and re-calling the model, bounded by a maximum retry count. The model can revise its response given explicit feedback about why its previous output was rejected.

## Decision

### Architecture: handler-level reflexion loop

The reflexion loop lives in `handleNonStream` (and future: `handleStream`), not inside the guardrail plugin or the pipeline engine. Rationale:

- The guardrail plugin's `ProcessResponse` must remain a pure post-plugin: it checks, sets state, returns an error. It does not own retry logic.
- The handler already owns the `providerCall` closure and the response-write path — it is the natural coordination point.
- The engine's `Process` method re-runs pre-plugins on each reflexion attempt (auth, rate-limit, guardrail pre-check). This is a V1 tradeoff documented below.

### Detection of post-stage guardrail blocks

A post-stage guardrail block is detected in `handleNonStream` by:

```
engine.Process() returned nil  (pre-stage passed)
AND rc.Response == nil         (guardrail nulled the response)
AND rc.Flags.GuardrailTriggered
```

Pre-stage guardrail blocks return a non-nil error from `engine.Process` and are NOT reflexion candidates — they reject the *input*, not the output.

### Feedback injection

The block reason (`rc.GuardrailResults[0].Message`) is formatted into a user-turn message appended to the conversation:

> "Your previous response was blocked by a content policy guardrail. Please revise your response to comply. Reason: {reason}"

The template is configurable via `guardrails.reflexion.feedback_template` in `config.yaml`.

### Configuration

```yaml
guardrails:
  reflexion:
    enabled: true
    max_attempts: 3        # hard cap at 5
    feedback_template: ""  # use default if empty
```

Disabled by default. `MaxAttempts` is hard-capped at 5 to prevent runaway loops even if misconfigured.

## Consequences

### Accepted V1 tradeoffs

1. **Pre-plugins re-run on each attempt**: auth, rate-limit, and budget checks all fire again. For rate-limiting, each reflexion attempt consumes a rate-limit slot. Operators should set `MaxAttempts` conservatively. A future version can add a dedicated `engine.ProcessRetry` that skips pre-plugins.

2. **Blocked provider calls are not billed to the client**: the cost plugin only records cost when `rc.Response != nil`. Reflexion attempts that are blocked (model still violates policy) incur provider cost that is not passed through. This is conservative from the client's perspective.

3. **Streaming reflexion not yet implemented**: `handleStream` continues to return the guardrail block error immediately. Streaming reflexion requires buffering the full stream before checking, which is a larger change tracked separately.

4. **No exponential back-off**: retries are immediate. Provider errors during reflexion abort immediately (not retried within the reflexion loop).

### Benefits

- Response quality: model gets explicit feedback to correct its output rather than an opaque block.
- Client transparency: successful reflexion is indicated by `x-agentcc-reflexion-attempt` header.
- Operator control: disabled by default, configurable per-gateway.

## Formal verification

- `docs/tla/GuardrailReflexion.tla` — TLA+ spec proving: loop terminates, attempt count is bounded, success is recorded on first passing response.
- `agentcc-gateway/formal_tests/test_reflexion_z3.py` — Z3 proofs: bounded termination impossibility of infinite loop, monotonic attempt counter.
- `agentcc-gateway/formal_tests/test_reflexion_hypothesis.py` — Hypothesis property tests: max-attempts invariant, feedback accumulation, success on first non-blocked response.

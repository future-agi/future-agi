# Guardrail Reflexion — Specification

## Overview

When a post-stage guardrail blocks the model's response, the gateway can inject the
block reason back into the conversation and re-call the model. This is **reflexion**:
the model gets a chance to self-correct given explicit feedback about the policy
violation rather than receiving a silent 403 block.

Reflexion is disabled by default and opt-in via `config.yaml`.

## Trigger condition

Reflexion fires when ALL of the following are true after `engine.Process`:

1. `engine.Process` returned `nil` (no pre-stage guardrail block or provider error)
2. `rc.Response == nil` (post-stage guardrail nulled the response)
3. `rc.Flags.GuardrailTriggered == true`

Pre-stage guardrail blocks (bad request content) are NOT reflexion candidates —
they return a non-nil error from `engine.Process` and represent an input problem,
not an output problem.

## Algorithm

```
attempt := 1
loop:
  blockMsg := rc.GuardrailResults[0].Message  (or fallback)
  feedback := format(feedbackTemplate, blockMsg)
  rc.Request.Messages = append(..., {role: "user", content: feedback})
  rc.Metadata["reflexion_attempt"] = attempt

  reset rc: Response=nil, Flags.GuardrailTriggered=false, GuardrailResults=[], Errors=[]
  engine.Process(ctx, rc, providerCall)

  if rc.Response != nil:
    rc.Metadata["reflexion_success"] = "true"
    return nil  // success

  if !rc.Flags.GuardrailTriggered:
    break       // non-guardrail error; abort

  if attempt >= MaxAttempts:
    break

  attempt++

return ErrGuardrailBlocked("content_blocked", blockMsg)
```

## Invariants

| Property | Description |
|----------|-------------|
| **Bounded** | `attempt <= min(MaxAttempts, 5)` always |
| **Monotone** | attempt counter only increases |
| **Feedback grows** | appended messages are never removed |
| **Terminal absorbing** | once done or failed, no further transitions |
| **Disabled → immediate block** | when `MaxAttempts=0` or `enabled=false`, error returned immediately |

Formally proved in `docs/tla/GuardrailReflexion.tla` (TLA+),
`agentcc-gateway/formal_tests/test_reflexion_z3.py` (Z3), and
`agentcc-gateway/formal_tests/test_reflexion_hypothesis.py` (Hypothesis).

## Configuration

```yaml
guardrails:
  reflexion:
    enabled: true           # opt-in
    max_attempts: 3         # hard-capped at 5
    feedback_template: ""   # use default if empty
```

Default feedback template:
> "Your previous response was blocked by a content policy guardrail. Please revise your response to comply. Reason: {reason}"

## Scope

- **Non-streaming only** (V1): streaming reflexion requires buffering the full stream before checking, tracked separately.
- **Post-stage only**: pre-stage guardrail blocks on the request are not retried.
- **Handler-level**: the loop is in `handleNonStream`, not the engine or guardrail plugin.

## Tradeoffs (see ADR 028)

- Pre-plugins re-run on each reflexion attempt (auth, rate-limit).
- Blocked attempts are not billed to the client (no usage data since response is nil).
- No exponential back-off; retries are immediate.

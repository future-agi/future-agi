# Guardrail retry with feedback

## User-facing behavior

A synchronous response check can ask the gateway to repair a blocked answer before
returning anything to the client. Reject remains the default. Request-stage blocks,
check execution failures and provider errors do not trigger repair. Existing
provider retry/failover policy still applies to provider errors.

The initial implementation supports **`/v1/chat/completions`**, including its
streaming form. An applicable reflection rule rejects unsupported text-generation
routes before calling the provider: native Messages/GenAI, Responses and legacy
Completions need separate adapters. Do not enable the rule for those callers
until they use the supported chat route. This does not remove unrelated
embeddings, health or admin capabilities.

In **Gateway → Guardrails → Rules**, edit a check, select **Response** and **Block**,
then choose **Retry with feedback**. Save the check, then **Save & Activate** the
configuration. A check's existing provider settings and encrypted credentials are
preserved when changing these controls.

The equivalent per-check configuration lives in the existing `config` object:

```json
{
  "name": "data-leakage-prevention",
  "action": "block",
  "config": {
    "stage": "post",
    "on_block": "reflect",
    "reflect_max_attempts": 1,
    "reflect_debug": false
  }
}
```

For operator-managed YAML, put these same controls inside the rule's `config`,
with the rule's top-level `stage: post`, `mode: sync` and `action: block`.

| Control | Default | Validation |
| --- | --- | --- |
| `on_block` | `reject` | `reject` or `reflect` |
| `reflect_max_attempts` | `1` | Integer-valued number from 1 through 5; no coercion or clamping |
| `reflect_debug` | `false` | Boolean |
| `stage` | Check's native stage | `pre` or `post`; response-only checks cannot run before generation |

The retry limit counts **additional generations**, not the original response.
One allows an original answer and one repair. Normal transport retry/failover can
add upstream calls, so this is not a limit on all upstream HTTP requests.

Legacy rules without an explicit `config.stage` keep their native stage. The old
UI stored a top-level `pre` default even for native response checks; that historical
value is not reinterpreted on a routine save. New reflection settings can infer
an explicitly declared stage, and the UI writes `config.stage` when choosing a
stage or enabling repair. `config.mode` remains provider-specific, not a shared
execution-mode switch. Prompt injection, topic restriction, language detection
and system-prompt protection currently inspect only requests, so response-stage
settings are rejected for those checks rather than pretending to validate output.

## Execution and safety

When the gateway has a control-plane URL, it initializes the managed guardrail
plugin and registry even if the operator's global `guardrails.enabled` flag is
false. That flag gates only operator-defined static rules. A tenant's enabled
checks synced from the control plane must not be silently ignored because the
operator has no static rules. With neither static guardrails enabled nor a
control-plane URL, the plugin stays off.

1. Run ordinary request admission and request checks.
2. Generate a candidate and settle its observed provider usage before a response
   check can discard it.
3. Run synchronous response checks against the candidate. A rejecting rule wins
   over a reflecting rule. The smallest retry allowance encountered across the
   request remains the limit; a later rule cannot reset it.
4. On a repairable block, build a private copy of the original request with the
   rejected assistant answer and check feedback. Do not mutate the request that
   validators, final logs or asynchronous observers see.
5. Recheck admission before the additional generation. Stop at the first passing
   candidate, exhaustion, denial, deadline, cancellation or provider error.
6. Run final observers once. Never cache a rejected candidate.

Repair supports single-choice text answers. Blocked tool-call, reasoning and
multi-choice answers fail closed without constructing synthetic tool results or
invalid conversation history. A passing answer of those types is not rejected
merely because repair is enabled.

## Streaming

Opting into repair buffers each complete candidate before sending response
headers or chunks. Failed candidates are discarded. Only a passing candidate is
replayed, followed by the final usage/metadata chunk and `[DONE]`. This adds
latency and removes incremental delivery while the model generates.

Each candidate is bounded to **2 MiB of encoded chunks and 8,192 frames**.
Overflow, incomplete streams and cancellation fail closed. Usage that the
provider did not report is marked incomplete, not guessed. Ordinary streams
without a reflection rule retain their existing streaming path; enabling this
feature is not a claim that unbuffered streams can retract already delivered text.

## Usage, limits and observability

Every observed generation, including a rejected one, incurs provider usage.
Additional repairs are subject to credits, budgets and rate limits. Accounting
must occur before discarding blocked content, and a denied repair must not charge
the previous attempt a second time. This corrects the old loss of blocked-call
accounting; it does not change pricing rates or introduce budget reservations.

Aggregate usage and spend include observed attempts, priced using each actual
serving model, including model fallback. Missing provider usage is flagged with
`usage_incomplete` and `x-agentcc-usage-incomplete`; cancellation or transport
failure can make the total a lower bound.

Request metadata records bounded, body-free attempt accounting. Debug opt-in adds
retry diagnostics and headers; it does not persist raw rejected answers, check
messages that may quote them, or credentials. Existing request-log metadata
rendering displays these controls without a new database column.

## Verification

Production tests live in:

- `agentcc-gateway/internal/server/reflexion*_test.go`: actual HTTP handlers and
  deterministic provider adapters, buffered streaming, exhaustion and accounting.
- `agentcc-gateway/internal/guardrails/reflexion_test.go`: rule policy and tenant
  isolation.
- `agentcc-gateway/internal/reflexion/policy_test.go`: strict configuration bounds.
- `futureagi/agentcc/tests/test_guardrail_reflexion.py`: serializers, activation,
  credential-safe persistence and the existing strict gateway contract.
- `frontend/src/sections/gateway/settings/GuardrailCheckDialog.test.jsx`: real
  dialog controls, invalid limits and provider-config preservation.

The earlier copied retry models and standalone proof workflow were removed:
proving a separate toy loop does not verify the production handler. Test files
and commands alone do not establish a passing run; the PR verification report
must include the tested commit and actual results, plus real UI/API/gateway
before-and-after evidence.

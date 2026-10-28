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

The control plane validates retry settings before activation. The gateway also
validates single-org `PUT` updates before writing anything: invalid enabled checks
return `400 invalid_guardrail_config` without changing stored configs or caches,
including for a new tenant. Bulk loads and control-plane sync store configs as
received, without gateway admission. If an invalid retry configuration or an
unresolved reflecting check reaches storage, request-time guardrail validation
fails closed with `400 invalid_guardrail_config` before calling a provider.

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

The native text scanners (input validation, keyword blocklist, PII detection,
content moderation and secret detection) normally read request and response text
together. When a tenant runs one at the response stage, or a static rule with
repair enabled runs one there, it receives the candidate answer without the
prompt, so a prompt cannot satisfy a required pattern or fail every repair.
Expressions, webhooks, tool permissions and external evaluators keep the full
request because they may compare it with the answer. Static rules without repair
keep their existing input.

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
3. Run synchronous response checks against the candidate. An effective reject-only
   block ends post-stage evaluation; later checks, including tenant checks after a
   static block, are skipped. Repairable blocks continue evaluation so later
   reject-only or operational blocks can veto repair. The smallest retry allowance
   encountered across the request remains the limit; a later rule cannot reset it.
4. On a repairable block, build a private copy of the original request with the
   rejected assistant answer and check feedback. Do not mutate the request that
   validators, final logs or asynchronous observers see.
5. Recheck admission before the additional generation. Stop at the first passing
   candidate, exhaustion, denial, deadline, cancellation or provider error.
   A provider error on a repair request can quote the private answer and
   feedback, so the client, request log and observers receive only its status
   with gateway-generated type, code and message.
6. Run final observers once. Never cache a rejected candidate.

A cached response is revalidated against the current checks. If it fails a
reflecting rule, the gateway fails closed with `cached_response_blocked` rather
than silently starting paid repair work on a cache hit. Send `Cache-Control:
no-cache` to regenerate through the candidate controller; only a passing answer
can replace the cached entry.

Feedback instructs the model to follow the checks' intent rather than bypass them
with alternate spellings, lookalike or invisible characters, spacing or encodings,
and to decline briefly if the request cannot be answered within those checks.
This instruction is not an enforcement guarantee: lexical checks such as keyword
blocklists can still be evaded with lookalike characters. Prefer semantic checks
for repair when policy intent, rather than exact text, must be enforced.

A post-stage rejection, with or without repair, also replaces the messages of
that stage's check results and restores the earlier warning metadata, so a
warning that quotes the blocked answer is not logged.

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
Overflow, incomplete streams, in-band error frames and cancellation fail closed;
an error frame is never replayed. Usage that the
provider did not report is marked incomplete, not guessed. Ordinary streams
without a reflection rule retain their existing streaming path; enabling this
feature is not a claim that unbuffered streams can retract already delivered text.

## Usage, limits and observability

Every observed generation, including a rejected one, incurs provider usage.
Additional repairs are subject to credits, budgets and rate limits. Accounting
must occur before discarding blocked content, and a denied repair must not charge
the previous attempt a second time. This corrects the old loss of blocked-call
accounting; it does not change pricing rates or introduce budget reservations.

Aggregate usage and spend include observed attempts. Each attempt is priced by
the model the provider reported, or by the upstream request model when the
response omits one, with the caller's requested model as the fallback, as in
ordinary accounting. Budgets and repair admission use the caller's requested
model, including when routing or model fallback changes the upstream model.
Missing provider usage is flagged with `usage_incomplete` and
`x-agentcc-usage-incomplete`; cancellation or transport failure can make the
total a lower bound.

Request metadata records bounded, body-free attempt accounting. Debug opt-in adds
retry diagnostics and headers; it does not persist raw rejected answers, check
messages that may quote them, or credentials. Existing request-log metadata
rendering displays these controls without a new database column. Provider stream
and error logs record identifiers and status, not upstream payload text.

## Verification

Production tests live in:

- `agentcc-gateway/internal/server/reflexion*_test.go`: actual HTTP handlers and
  deterministic provider adapters, buffered streaming, exhaustion and accounting.
- `agentcc-gateway/internal/guardrails/reflexion_test.go`: rule policy and tenant
  isolation; `post_input*_test.go`: response-only input for native scanners and
  full context for evaluators that compare request and response.
- `agentcc-gateway/internal/pipeline/reflexion_error_test.go`: repair-error
  sanitizing and attempt model identity.
- `agentcc-gateway/internal/providers/provider_logging_test.go`: provider logs
  without upstream payloads.
- `agentcc-gateway/internal/reflexion/policy_test.go`: strict configuration bounds.
- `futureagi/agentcc/tests/test_guardrail_reflexion.py`: serializers, activation,
  credential-safe persistence and the existing strict gateway contract.
- `frontend/src/sections/gateway/settings/GuardrailCheckDialog.test.jsx`: real
  dialog controls, invalid limits and provider-config preservation;
  `guardrails/GuardrailManagementSection.test.jsx`: Overview edits that clear
  retry settings when a rule is no longer eligible.

The earlier copied retry models and standalone proof workflow were removed:
proving a separate toy loop does not verify the production handler. Test files
and commands alone do not establish a passing run; the PR verification report
must include the tested commit and actual results, plus real UI/API/gateway
before-and-after evidence.

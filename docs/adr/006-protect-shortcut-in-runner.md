# ADR 006 — Protect model shortcut lives in the runner, not the template

**Status**: Accepted
**Evidence**: commit `433d6c5` ("feat(eval): route protect and protect_flash calls through DeterministicEvaluator"); `evaluations/engine/runner.py:103-109`

## Context

The `protect` and `protect_flash` eval modes route guardrail checks through
`DeterministicEvaluator` instead of whatever the template's `eval_type_id` says. This override
needed to live somewhere.

Two options:
1. On the `EvalTemplate` — a `call_type` field on the template itself that the runner checks.
2. In the runner — reading `call_type` from the runtime `inputs` dict.

## Decision

The shortcut lives in the runner and reads from `request.inputs["call_type"]` (commit `433d6c5`,
"feat(eval): route protect and protect_flash calls through DeterministicEvaluator"):

```python
call_type = request.inputs.get("call_type", "")
if call_type in ("protect", "protect_flash") and eval_type_id != "DeterministicEvaluator":
    eval_type_id = "DeterministicEvaluator"
```

## Why runtime inputs, not template property

`call_type` describes how the eval is being invoked at this moment, not what kind of eval the
template defines. The same template can be used as a protect guardrail in one request and as a
normal evaluation in another. Making it a template property would force users to create separate
templates for protect vs non-protect usage.

Guardrail context (`call_type = "protect"`) is injected by the gateway before the eval reaches
the runner. It is runtime context, not configuration.

## Consequences

- Any caller that wants to trigger the protect shortcut must inject `call_type` into the inputs
  dict before calling `run_eval()`. It is not automatic.
- The shortcut also patches `eval_template.config` in-place
  (`eval_template.config = {**eval_template.config, "eval_type_id": "DeterministicEvaluator"}`)
  so that `format_eval_value` uses the DeterministicEvaluator formatting branch. This mutates
  the config dict for the duration of the request — callers that reuse the template object
  across requests should be aware.

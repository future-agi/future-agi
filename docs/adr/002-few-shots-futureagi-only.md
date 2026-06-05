# ADR 002 — Few-shot RAG injection is FutureAGI-eval-only

**Status**: Accepted
**Evidence**: `evaluations/constants.py:9-14` (comment: "special config preparation: criteria injection, few-shot retrieval"); `evaluations/engine/params.py:52-80`

## Context

The evaluation engine supports two kinds of few-shot examples:

1. **Static few-shot examples** configured on the template (used by `CustomPromptEvaluator` via
   `eval_template.config["few_shot_examples"]` — baked into the instance at config time).
2. **Dynamic RAG-retrieved few-shots** — examples retrieved at run time from `EmbeddingManager`
   using the current input's vector similarity against past feedback. These are injected as the
   `few_shots` kwarg into the evaluator's `.run()` call.

## Decision

Dynamic RAG few-shot injection (`few_shots` kwarg) is only performed for FutureAGI eval types
(`RankingEvaluator`, `DeterministicEvaluator`). All other eval types — `CustomPromptEvaluator`,
`AgentEvaluator`, `CustomCodeEval`, and all function evals — do not receive `few_shots`.

## Why

FutureAGI's internal Turing models (`turing_large`, `turing_small`, `turing_flash`) are trained
to accept and use a `few_shots` parameter in their prompt construction. Passing `few_shots` to
a `CustomPromptEvaluator` would inject it into an arbitrary user-defined prompt where the
template has no slot for it, producing unpredictable results or a `TypeError` if the evaluator
doesn't accept `**kwargs`.

`CustomPromptEvaluator` handles its own static few-shots via the template config.

## Consequences

- Callers of `prepare_run_params` with `is_futureagi=False` will never see a `few_shots` key
  injected — even if `organization_id` is provided. This is correct.
- If a custom eval type ever needs dynamic RAG retrieval, it must either: (a) set
  `is_futureagi=True` (wrong), or (b) retrieve examples itself inside the evaluator. There is
  currently no clean extension point for non-FutureAGI dynamic few-shots.

# Counterfactual trace replay

Counterfactual replay is a promotion gate for candidate changes to an AI agent.
It runs production-shaped requests through a new model, prompt, routing policy,
tool policy, or guardrail, evaluates the candidate against the current baseline,
and rejects promotion when quality or reliability regresses.

The implementation lives in `agentic_eval.core.replay` and has no provider or
network dependency. A candidate callable can invoke the Future AGI Gateway, a
hosted provider, or a local OpenAI-compatible runtime such as MLX-LM, oMLX,
Ollama, or llama.cpp.

## Example

```python
import asyncio

from agentic_eval.core.replay import (
    CounterfactualReplay,
    Evaluation,
    RegressionPolicy,
    ReplayCase,
)


async def candidate(request):
    # Call a hosted endpoint or an OpenAI-compatible local runtime here.
    return await model_client.complete(**request)


def task_quality(output, baseline, case):
    candidate_score = score_task(output)
    baseline_score = score_task(baseline)
    return Evaluation(
        name="task-quality",
        score=candidate_score,
        passed=candidate_score >= 0.8,
        baseline_score=baseline_score,
        details={"trace_id": case.metadata.get("trace_id")},
    )


cases = [
    ReplayCase(
        case_id="trace-001",
        request={"messages": [{"role": "user", "content": "..."}]},
        baseline=current_production_output,
        metadata={"trace_id": "trace-001", "split": "held-out"},
    )
]

report = asyncio.run(
    CounterfactualReplay(
        candidate,
        [task_quality],
        concurrency=8,
        policy=RegressionPolicy(
            minimum_case_count=50,
            minimum_evaluation_count=50,
            minimum_pass_rate=0.95,
            maximum_error_rate=0.01,
            maximum_regression_rate=0.02,
            maximum_mean_score_drop=0.01,
        ),
    ).run(cases)
)

report.raise_for_regressions()
```

## Determinism and isolation

- Results retain input order even when candidate calls finish out of order.
- Candidate and evaluator work share one concurrency ceiling, so an expensive
  evaluator cannot silently exceed the configured replay bound.
- Identical full requests share one candidate execution by default.
- Evaluation remains case-specific, so duplicated requests can still have
  different baselines and acceptance expectations.
- Requests that differ only by credentials never share candidate execution.
  The exported public fingerprint redacts credential values, while a separate
  non-exported digest controls execution reuse.
- Requests, baselines, metadata, candidate outputs, and evaluator case objects
  are deep-copied so mutation does not contaminate later cases or evaluators.
  Values that cannot be copied fail before shared mutable state is exposed; an
  uncopyable candidate output becomes an isolated structured replay error.

## Promotion policy

`RegressionPolicy` can fail a promotion on:

- too few replay cases;
- too few completed evaluations;
- insufficient pass rate;
- excessive candidate or evaluator error rate;
- excessive score-regression rate; or
- excessive mean score drop.

`minimum_evaluation_count` defaults to one, so a candidate-only smoke run is not
promotable unless the caller explicitly opts out. `score_regression_tolerance`
prevents insignificant floating-point noise from counting as a regression.
`ReplayReport.violations` records every failed condition rather than only the
first one.

For a held-out gate, pass only held-out cases to the replay run. Training cases
may be used during candidate generation, but they should not decide promotion.
The default policy also requires at least one completed evaluation, so a
successful candidate call without a quality or safety evaluator cannot be
promoted accidentally. Set `minimum_evaluation_count=0` only for an explicit
candidate-execution smoke test.

Candidate and evaluator timeouts cancel asynchronous callables. Replay-level
timeouts are rejected for synchronous callables because Python cannot forcibly
stop a function already running in a worker thread; releasing the concurrency
permit while that thread continued would violate the configured global bound.
Synchronous integrations must enforce their timeout in the downstream client,
or expose an asynchronous wrapper and use the replay-level timeout.

## Privacy defaults

The engine recursively redacts credential-shaped keys from public request
fingerprints and from any diagnostic fields that are explicitly exported.
Common forms such as `Authorization`, `api_key`, provider-specific `*_api_key`,
tokens, cookies, passwords, and secret keys are covered. Deployment-specific
names can be added with `additional_sensitive_keys`.

`ReplayReport.to_dict()` and `to_json()` omit candidate outputs, exception
messages, case metadata, and evaluator details by default. Any of those fields
can contain prompts, user content, provider URLs, or credentials. Enable only
the fields required for a controlled diagnostic artifact:

```python
payload = report.to_dict(
    include_outputs=True,
    include_error_messages=True,
    include_metadata=True,
    include_evaluation_details=True,
)
```

To retain exception messages at all, the replay engine must also be constructed
with `capture_error_messages=True`. Captured messages are sanitized against
credentials found in the replay case and common authorization/key patterns.
Omission remains the safest setting for arbitrary provider exception text.

Structurally known output values are redacted recursively. An unsupported
object is represented by its type name only; its `repr()` is never exported,
because arbitrary representations can contain credentials or execute unsafe
formatting code.

## Self-healing loop

A safe automated repair loop can use this sequence:

1. Convert failed or low-scoring traces into `ReplayCase` objects.
2. Generate a candidate prompt, model, routing, tool, or guardrail change.
3. Replay a held-out production-shaped suite against the candidate.
4. Evaluate task quality, safety, cost, and tool correctness.
5. Apply `RegressionPolicy` as the promotion gate.
6. Store the redacted report for auditability.
7. Promote accepted candidates and reject or revise the rest.

This establishes the verifier boundary without coupling the replay engine to a
specific optimizer, provider, or deployment runtime.

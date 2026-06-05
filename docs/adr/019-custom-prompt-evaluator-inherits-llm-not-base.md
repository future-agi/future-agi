---
status: Problematic — filed as issue #314
date: 2026-05-08
---

# ADR 019 — `CustomPromptEvaluator` inherits from `LLM`, not `BaseEvaluator`

## Evidence

`futureagi/agentic_eval/core_evals/fi_evals/llm/custom_prompt_evaluator/evaluator.py`:
`class CustomPromptEvaluator(LLM):` — not `BaseEvaluator`.

Missing: `metric_ids` property, `required_args` property, `is_failure()` method.

## Context

`CustomPromptEvaluator` was implemented early as part of the `LLM` class hierarchy. When
`BaseEvaluator` was formalised as the abstract protocol, `CustomPromptEvaluator` was not
migrated.

## Decision

`CustomPromptEvaluator` shares the `run()` and `run_batch()` implementations from `LLM`
via inheritance. It works because `LLM.run()` calls `_evaluate()`, and
`CustomPromptEvaluator` implements `_evaluate()`. The registry accepts it because
`get_eval_class()` looks up by `cls.name`, not by `isinstance(cls, BaseEvaluator)`.

## Why

Migration cost: `CustomPromptEvaluator` is the most-used evaluator class. Refactoring
it to inherit from `BaseEvaluator` risked changing its runtime behaviour through
`super()` resolution order changes.

## Consequences

- Code that calls `isinstance(evaluator, BaseEvaluator)` will return `False` for
  `CustomPromptEvaluator` — violating the Liskov Substitution Principle.
- `metric_ids` and `required_args` are absent, causing `AttributeError` in any caller
  that assumes all evaluators conform to the full protocol.
- Filed as issue #314.

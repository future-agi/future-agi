from __future__ import annotations

from typing import Any

from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from agent_playground.services.evaluation import (
    FAIL_CONTINUE,
    FAIL_ROUTE_FALLBACK,
    FAIL_STOP,
    coerce_threshold,
    run_evaluation_batch,
)


class EvaluationRunner(BaseNodeRunner):
    """Execute platform eval templates as an Agent Playground node."""

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        evaluators = config.get("evaluators") or []
        threshold = coerce_threshold(config.get("threshold", 0.5))
        fail_action = config.get("fail_action", FAIL_CONTINUE)
        if fail_action not in {FAIL_CONTINUE, FAIL_STOP, FAIL_ROUTE_FALLBACK}:
            raise ValueError(f"Unsupported evaluation fail_action: {fail_action}")

        summary = run_evaluation_batch(
            evaluators=evaluators,
            inputs=inputs,
            execution_context=execution_context,
            threshold=threshold,
        )
        summary["fail_action"] = fail_action

        if not summary["passed"] and fail_action == FAIL_STOP:
            raise ValueError(
                "Evaluation failed: " f"score={summary['score']}, threshold={threshold}"
            )

        passthrough = inputs.get("input")
        route_fallback = not summary["passed"] and fail_action == FAIL_ROUTE_FALLBACK
        return {
            "evaluation_result": summary,
            "passthrough": None if route_fallback else passthrough,
            "fallback": passthrough if route_fallback else None,
        }


register_runner("evaluation", EvaluationRunner())

from __future__ import annotations

from statistics import mean
from typing import Any

from agent_playground.models.choices import PortDirection
from agent_playground.models.execution_data import ExecutionData

FAIL_CONTINUE = "continue"
FAIL_STOP = "stop"
FAIL_ROUTE_FALLBACK = "route_fallback"

FIELD_ALIASES = {
    "actual": "input",
    "answer": "input",
    "code": "input",
    "expected": "reference",
    "expected_response": "reference",
    "output": "input",
    "prompt": "input",
    "query": "input",
    "response": "input",
    "text": "input",
}
_MISSING = object()


def run_evaluation_batch(
    evaluators: list[dict[str, Any]],
    inputs: dict[str, Any],
    execution_context: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    if not evaluators:
        raise ValueError("At least one evaluator is required")

    results = [
        _run_evaluator(spec, inputs, execution_context, threshold)
        for spec in evaluators
    ]
    return _summarize_results(results, threshold)


def run_agent_evaluation_batch(
    evaluators: list[dict[str, Any]],
    graph_execution,
    execution_context: dict[str, Any],
    threshold: float,
    fallback_mappings: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not evaluators:
        raise ValueError("At least one evaluator is required")

    output_payloads = _execution_output_payloads(graph_execution)
    results = []
    for spec in evaluators:
        mappings = _eval_mapping(spec) or fallback_mappings or {}
        inputs = _resolve_source_mappings(output_payloads, mappings)
        results.append(_run_evaluator(spec, inputs, execution_context, threshold))

    return _summarize_results(results, threshold)


def _summarize_results(
    results: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    numeric_scores = [
        result["score"]
        for result in results
        if isinstance(result["score"], int | float)
    ]
    aggregate_score = mean(numeric_scores) if numeric_scores else None
    passed = all(result["passed"] for result in results) and (
        aggregate_score is None or aggregate_score >= threshold
    )
    return {
        "score": aggregate_score,
        "passed": passed,
        "threshold": threshold,
        "results": results,
    }


def _resolve_source_mappings(
    output_payloads: dict[str, Any],
    mappings: dict[str, str],
) -> dict[str, Any]:
    if not mappings:
        raise ValueError("Evaluation mappings are required")

    resolved = {}
    missing = []
    for eval_key, source in mappings.items():
        payload = output_payloads.get(source, _MISSING)
        if payload is _MISSING:
            missing.append(source)
            continue
        resolved[eval_key] = payload

    if missing:
        raise ValueError(
            f"Missing execution outputs for mappings: {', '.join(missing)}"
        )
    return resolved


def _execution_output_payloads(graph_execution) -> dict[str, Any]:
    rows = (
        ExecutionData.no_workspace_objects.filter(
            node_execution__graph_execution=graph_execution,
            port__direction=PortDirection.OUTPUT,
        )
        .select_related("node_execution__node", "port")
        .order_by("node_execution__node_id", "port__key")
    )
    return {f"{row.node_execution.node_id}.{row.port.key}": row.payload for row in rows}


def _run_evaluator(
    spec: dict[str, Any],
    inputs: dict[str, Any],
    execution_context: dict[str, Any],
    threshold: float,
) -> dict[str, Any]:
    template = _load_eval_template(spec)
    resolved_inputs = _resolve_eval_inputs(template, spec, inputs)
    runtime_config = dict(spec.get("config") or {})
    runtime_config.pop("mapping", None)

    from evaluations.engine.runner import EvalRequest, run_eval

    result = run_eval(
        EvalRequest(
            eval_template=template,
            inputs=resolved_inputs,
            model=spec.get("model") or runtime_config.get("model"),
            runtime_config=runtime_config or None,
            organization_id=execution_context.get("organization_id"),
            workspace_id=execution_context.get("workspace_id"),
        )
    )

    score = score_value(result.value)
    passed = result.failure is None and (
        bool(result.value) if score is None else score >= threshold
    )
    return {
        "template_id": str(template.id),
        "name": spec.get("name") or template.name,
        "value": result.value,
        "score": score,
        "passed": passed,
        "threshold": threshold,
        "reason": result.reason,
        "failure": result.failure,
        "output_type": result.output_type,
        "duration": result.duration,
        "model": result.model_used,
    }


def _load_eval_template(spec: dict[str, Any]):
    template_id = (
        spec.get("templateId")
        or spec.get("template_id")
        or spec.get("eval_template_id")
        or spec.get("id")
    )
    if not template_id:
        raise ValueError("Evaluator config missing templateId")

    from model_hub.models.evals_metric import EvalTemplate

    try:
        return EvalTemplate.no_workspace_objects.get(id=template_id, deleted=False)
    except EvalTemplate.DoesNotExist as exc:
        raise ValueError(f"Evaluation template not found: {template_id}") from exc


def _resolve_eval_inputs(
    template,
    spec: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    mapping = _eval_mapping(spec)
    keys = (
        list(template.config.get("required_keys") or [])
        + list(template.config.get("optional_keys") or [])
        + list(mapping.keys())
    )
    resolved: dict[str, Any] = {}
    for key in dict.fromkeys(keys):
        source = mapping.get(key) or FIELD_ALIASES.get(key) or key
        value = inputs.get(source)
        if value is None and source != key:
            value = inputs.get(key)
        if value is not None:
            resolved[key] = value
    return resolved


def _eval_mapping(spec: dict[str, Any]) -> dict[str, str]:
    return spec.get("mapping") or (spec.get("config") or {}).get("mapping") or {}


def coerce_threshold(value: Any) -> float:
    if value is None:
        raise ValueError("Evaluation threshold is required")
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Evaluation threshold must be a number") from exc
    if threshold < 0 or threshold > 1:
        raise ValueError("Evaluation threshold must be between 0 and 1")
    return threshold


def score_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value) / 100 if value > 1 and value <= 100 else float(value)
    if isinstance(value, dict):
        for key in ("score", "value"):
            score = score_value(value.get(key))
            if score is not None:
                return score
        if "passed" in value:
            return score_value(value["passed"])
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"pass", "passed", "true", "yes"}:
            return 1.0
        if lowered in {"fail", "failed", "false", "no"}:
            return 0.0
        try:
            return score_value(float(lowered))
        except ValueError:
            return None
    return None

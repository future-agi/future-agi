"""
Unified eval execution engine.

run_eval() is THE single function that all eval execution paths call.
It composes: registry → instance creation → param preparation → execution → formatting.

Callers handle their own:
- Input resolution (span attributes, dataset cells, transcript data)
- Cost tracking (APICallLog)
- Result persistence (EvalLogger, Evaluation, Cell, eval_outputs)
- Error handling and status updates
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from evaluations.constants import FUTUREAGI_EVAL_TYPES
from evaluations.engine.formatting import extract_raw_result, format_eval_value
from evaluations.engine.instance import create_eval_instance
from evaluations.engine.params import prepare_run_params
from evaluations.engine.registry import get_eval_class

logger = structlog.get_logger(__name__)


_REQUIRED_COST_KEYS = {"total_cost", "prompt_cost", "completion_cost"}
_REQUIRED_TOKEN_USAGE_KEYS = {
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
}
_OPTIONAL_TOKEN_USAGE_KEYS = {
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
}


def _valid_cost_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _valid_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sanitize_cost_payload(payload: Any) -> dict | None:
    if not isinstance(payload, dict) or not _REQUIRED_COST_KEYS.issubset(
        payload.keys()
    ):
        return None
    if not all(_valid_cost_number(payload[key]) for key in _REQUIRED_COST_KEYS):
        return None

    sanitized = {key: payload[key] for key in _REQUIRED_COST_KEYS}
    pricing_source = payload.get("pricing_source")
    if isinstance(pricing_source, str):
        sanitized["pricing_source"] = pricing_source
    return sanitized


def _sanitize_token_usage_payload(payload: Any) -> dict | None:
    if not isinstance(payload, dict) or not _REQUIRED_TOKEN_USAGE_KEYS.issubset(
        payload.keys()
    ):
        return None
    if not all(_valid_token_count(payload[key]) for key in _REQUIRED_TOKEN_USAGE_KEYS):
        return None

    sanitized = {key: payload[key] for key in _REQUIRED_TOKEN_USAGE_KEYS}
    for key in _OPTIONAL_TOKEN_USAGE_KEYS:
        value = payload.get(key)
        if _valid_token_count(value):
            sanitized[key] = value
    return sanitized


@dataclass
class EvalRequest:
    """Everything needed to run a single evaluation."""

    eval_template: Any  # EvalTemplate model instance
    inputs: dict[str, Any]  # Pre-resolved key→value mapping
    model: str | None = None  # Model override
    kb_id: str | None = None  # Knowledge base ID
    runtime_config: dict | None = None  # Per-run config overrides
    organization_id: str | None = None
    workspace_id: str | None = None
    version_number: int | None = None  # Specific template version

    # Advanced options
    config_overrides: dict = field(default_factory=dict)  # Extra config for instance
    skip_params_preparation: bool = (
        False  # If True, inputs are passed directly as run_params
    )
    criteria_override: str | None = None  # Override criteria text


@dataclass
class EvalResult:
    """Standardized output from any evaluation."""

    value: Any  # Formatted output (float, bool, str, list, dict)
    data: dict | None  # Raw input data echo
    reason: str | None  # Explanation text
    failure: str | None  # Error message if failed, else None
    runtime: float | None  # Execution time in seconds
    model_used: str | None  # Model actually used
    metrics: list | None  # Metric objects [{id, value}]
    metadata: dict | None  # Additional metadata
    output_type: str  # "score", "Pass/Fail", "choices", "reason", "numeric"

    # Timing (wall clock, includes network)
    start_time: float | None = None
    end_time: float | None = None
    duration: float | None = None

    # Cost tracking (prefer valid, complete response-level fields; fall back to
    # evaluator instance fields for compatibility).
    cost: dict | None = None
    token_usage: dict | None = None


def run_eval(request: EvalRequest) -> EvalResult:
    """
    Run a single evaluation.

    This is the single point of truth for:
    resolve class → create instance → prepare params → run → format.

    No result persistence or billing side effects. The returned EvalResult may
    include valid, complete response-level cost/token_usage fields, and falls back to
    evaluator instance fields for compatibility. Callers handle all other side
    effects.

    Args:
        request: EvalRequest with template, inputs, and config

    Returns:
        EvalResult with formatted value and raw result data

    Raises:
        ValueError: If eval_type_id is missing or evaluator class not found
    """
    eval_template = request.eval_template
    eval_type_id = eval_template.config.get("eval_type_id")

    if not eval_type_id:
        raise ValueError(
            f"eval_type_id not found in EvalTemplate config for '{eval_template.name}'"
        )

    call_type = request.inputs.get("call_type", "")
    # Set a default model for protect calls if not provided.
    if call_type in ("protect", "protect_flash") and not request.model:
        request.model = "protect_flash" if call_type == "protect_flash" else "protect"

    is_futureagi = eval_type_id in FUTUREAGI_EVAL_TYPES

    # 1. Look up evaluator class
    eval_class = get_eval_class(eval_type_id)

    # 2. Create instance
    config = dict(request.config_overrides)
    eval_instance, criteria = create_eval_instance(
        eval_class=eval_class,
        eval_template=eval_template,
        config=config,
        model=request.model,
        kb_id=request.kb_id,
        runtime_config=request.runtime_config,
        organization_id=request.organization_id,
        workspace_id=request.workspace_id,
        version_number=request.version_number,
        is_futureagi=is_futureagi,
    )

    # Use criteria from request if provided, else from instance creation
    if request.criteria_override:
        criteria = request.criteria_override

    # 3. Prepare run params
    if request.skip_params_preparation:
        run_params = dict(request.inputs)
    else:
        run_params = prepare_run_params(
            inputs=request.inputs,
            eval_template=eval_template,
            is_futureagi=is_futureagi,
            criteria=criteria,
            organization_id=request.organization_id,
            workspace_id=request.workspace_id,
        )

    # Ensure eval_name is available for protect calls.
    # Also forward max_tokens from inputs if present.
    if call_type in ("protect", "protect_flash"):
        run_params.setdefault("eval_name", eval_template.name)
        if "max_tokens" in request.inputs:
            run_params.setdefault("max_tokens", request.inputs["max_tokens"])

    if eval_type_id == "CustomCodeEval" and request.runtime_config:
        _runtime_params = (request.runtime_config or {}).get("params") or {}
        if isinstance(_runtime_params, dict):
            for _k, _v in _runtime_params.items():
                run_params.setdefault(_k, _v)

    # 3.5. Preprocess inputs (e.g., compute embeddings for CLIP/FID)
    from evaluations.engine.preprocessing import preprocess_inputs

    run_params = preprocess_inputs(eval_template.name, run_params)

    # 4. Execute
    start_time = time.time()
    raw_result = eval_instance.run(**run_params)
    end_time = time.time()

    # 5. Extract and format
    response = extract_raw_result(raw_result, eval_template)
    response["start_time"] = start_time
    response["end_time"] = end_time
    response["duration"] = end_time - start_time

    value = format_eval_value(response, eval_template)

    logger.info(
        "eval_executed",
        template=eval_template.name,
        eval_type=eval_type_id,
        output_type=response.get("output"),
        duration=round(response["duration"], 3),
    )

    response_cost = _sanitize_cost_payload(response.get("cost"))
    response_token_usage = _sanitize_token_usage_payload(response.get("token_usage"))
    cost: dict | None
    token_usage: dict | None
    if response_cost is not None and response_token_usage is not None:
        cost = response_cost
        token_usage = response_token_usage
    else:
        cost = getattr(eval_instance, "cost", None)
        token_usage = getattr(eval_instance, "token_usage", None)

    return EvalResult(
        value=value,
        data=response.get("data"),
        reason=response.get("reason"),
        failure=response.get("failure"),
        runtime=response.get("runtime"),
        model_used=response.get("model"),
        metrics=response.get("metrics"),
        metadata=response.get("metadata"),
        output_type=response.get("output", "score"),
        start_time=start_time,
        end_time=end_time,
        duration=end_time - start_time,
        cost=cost,
        token_usage=token_usage,
    )

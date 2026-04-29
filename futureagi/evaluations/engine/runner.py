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


def run_eval(request: EvalRequest) -> EvalResult:
    """
    Run a single evaluation.

    This is the single point of truth for:
    resolve class → create instance → prepare params → run → format.

    No result persistence. No cost tracking. Callers handle all side effects.

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
    if call_type in ("protect", "protect_flash"):
        run_params.setdefault("eval_name", eval_template.name)

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
    )

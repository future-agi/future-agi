"""Ground Truth Search tool exposed to LLM-driven evaluators.

Lets the agent retrieve human-annotated GT rows whose mapped inputs are
semantically similar to whatever the agent is currently evaluating. The
agent receives a structured text block - input columns first, then the
labelled eval output and (optional) explanation - and can use those as
reference judgments before producing its own verdict.

All retrieval logic lives in :class:`GroundTruthService`. This file is
the thin LLM-facing surface: validate the agent's call, fetch matches,
render them into a single string the LLM can read.
"""

from typing import Any

import structlog
from django.db import DatabaseError
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class GroundTruthSearchInput(PydanticBaseModel):
    ground_truth_id: str = Field(
        description="Ground truth dataset ID to search in"
    )
    inputs: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Multi-variable inputs matching the rule prompt's template "
            "variables. Preferred when the rule prompt has more than a "
            "single input variable."
        ),
    )
    query: str = Field(
        default="",
        description=(
            "Free-form text query. Kept as a fallback for single-variable "
            "prompts; prefer `inputs` when the rule prompt is "
            "multi-variable."
        ),
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of examples to return (default 5).",
    )


@register_tool
class GroundTruthSearchTool(BaseTool):
    name = "search_ground_truth"
    description = (
        "Search the ground truth dataset for relevant reference examples "
        "that have been evaluated by human experts. Returns similar "
        "cases with their labelled eval outputs and explanations. Use "
        "this to find calibration examples when evaluating similar inputs."
    )
    category = "web"
    input_model = GroundTruthSearchInput

    def execute(
        self, params: GroundTruthSearchInput, context: ToolContext
    ) -> ToolResult:
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.services.ground_truth_service import GroundTruthService

        try:
            gt = EvalGroundTruth.objects.get(
                id=params.ground_truth_id,
                organization=context.organization,
                workspace=context.workspace,
                deleted=False,
            )
        except EvalGroundTruth.DoesNotExist:
            return ToolResult.error(
                f"Ground truth dataset {params.ground_truth_id} not found."
            )

        resolved_inputs = _resolve_inputs(params, gt.variable_mapping)
        if not resolved_inputs:
            return ToolResult.error(
                "Provide either a non-empty `query` string or an `inputs` "
                "dict matching the rule prompt's template variables."
            )

        try:
            rows = GroundTruthService.retrieve_few_shot(
                gt=gt, inputs=resolved_inputs, max_results=params.max_results
            )
        except (DatabaseError, ConnectionError, ValueError) as exc:
            logger.exception(
                "ground_truth_search_failed",
                ground_truth_id=params.ground_truth_id,
                error=str(exc),
            )
            return ToolResult.error(f"Ground truth search failed: {exc}")

        if not rows:
            return ToolResult(
                content="No relevant ground truth examples found for this query.",
                data={"total_results": 0},
            )

        rendered = _render_for_agent(
            rows=rows,
            variable_mapping=gt.variable_mapping or {},
            role_mapping=gt.role_mapping or {},
        )
        logger.info(
            "ground_truth_search_tool_returned",
            ground_truth_id=params.ground_truth_id,
            total_results=len(rows),
        )
        return ToolResult(
            content=rendered, data={"total_results": len(rows)}
        )


def _resolve_inputs(
    params: GroundTruthSearchInput,
    variable_mapping: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project either ``inputs`` or the legacy ``query`` string into the
    per-variable dict the retrieval helper expects.

    Returns ``{}`` when neither field carries any signal - callers
    surface that as a 4xx-style error to the agent so it can retry
    with a real query.
    """
    if isinstance(params.inputs, dict) and any(
        _has_signal(v) for v in params.inputs.values()
    ):
        return {k: v for k, v in params.inputs.items() if _has_signal(v)}

    stripped = (params.query or "").strip()
    if not stripped:
        return {}

    # Legacy single-text-box query: fan it out to every mapped template
    # variable so per-column intersection still has something to match.
    if not variable_mapping:
        return {}
    return {var: stripped for var in variable_mapping.keys()}


def _has_signal(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _render_for_agent(
    rows: list[dict[str, Any]],
    variable_mapping: dict[str, Any],
    role_mapping: dict[str, Any],
) -> str:
    """Format retrieved rows into a single block the LLM can read.

    Each match gets:

    * INPUTS - one line per mapped template variable, labelled by
      ``{{var}} ← gt_column`` so the agent sees what was used
    * EVAL OUTPUT - the labelled verdict (the format it should match)
    * EVAL EXPLANATION - the reasoning the human gave, when present

    Unmapped columns are suppressed; they're noise in the retrieval
    context and tend to mislead the agent.
    """
    from model_hub.utils.ground_truth_retrieval import get_label_columns

    output_col, explanation_col = get_label_columns(role_mapping)
    input_pairs = _flatten_input_pairs(variable_mapping)

    lines: list[str] = [
        f"Found {len(rows)} reference example(s). The eval output column "
        "shows the format your final verdict must use.\n",
    ]
    for idx, row in enumerate(rows, 1):
        lines.append(f"=== Example {idx} ===")
        if input_pairs:
            lines.append("INPUTS")
            for tmpl_var, column in input_pairs:
                value = row.get(column)
                if value is None or (
                    isinstance(value, str) and not value.strip()
                ):
                    continue
                arrow = f"{{{{{tmpl_var}}}}} ← {column}"
                lines.append(f"  {arrow}: {value}")
        if output_col and row.get(output_col) is not None:
            lines.append(f"EVAL OUTPUT ({output_col}): {row[output_col]}")
        if explanation_col and row.get(explanation_col) is not None:
            lines.append(
                f"EVAL EXPLANATION ({explanation_col}): {row[explanation_col]}"
            )
        lines.append("")
    return "\n".join(lines)


def _flatten_input_pairs(
    variable_mapping: dict[str, Any],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for tmpl_var, value in (variable_mapping or {}).items():
        candidates = value if isinstance(value, list) else [value]
        for column in candidates:
            if column:
                pairs.append((tmpl_var, column))
    return pairs

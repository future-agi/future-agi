"""
Ground Truth Search tool for AI agent evaluators.

Searches a ground truth dataset using embedding similarity to find
relevant reference examples. Used by AgentEvaluator to access
human-annotated examples during evaluation.
"""

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class GroundTruthSearchInput(PydanticBaseModel):
    query: str = Field(
        description="Search query — describe what kind of examples you're looking for"
    )
    ground_truth_id: str = Field(description="Ground truth dataset ID to search in")
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of examples to return (default 5)",
    )


def _ground_truth_candidates_result(
    context: ToolContext,
    title: str = "Ground Truth Required",
    detail: str = "",
) -> ToolResult:
    from model_hub.models.evals_metric import EvalGroundTruth

    ground_truths = list(
        EvalGroundTruth.objects.filter(
            organization=context.organization,
            deleted=False,
        )
        .select_related("eval_template")
        .order_by("-created_at")[:10]
    )
    rows = [
        [
            f"`{ground_truth.id}`",
            ground_truth.name,
            ground_truth.eval_template.name if ground_truth.eval_template else "—",
            str(ground_truth.row_count),
            ground_truth.embedding_status,
        ]
        for ground_truth in ground_truths
    ]
    body = detail or "Choose a ground truth dataset ID."
    body += "\n\n" + (
        markdown_table(["ID", "Name", "Eval Template", "Rows", "Embedding"], rows)
        if rows
        else "No ground truth datasets found."
    )
    return ToolResult(
        content=section(title, body),
        data={
            "requires_ground_truth_id": True,
            "ground_truths": [
                {
                    "id": str(ground_truth.id),
                    "name": ground_truth.name,
                    "row_count": ground_truth.row_count,
                    "embedding_status": ground_truth.embedding_status,
                }
                for ground_truth in ground_truths
            ],
        },
    )


@register_tool
class GroundTruthSearchTool(BaseTool):
    name = "search_ground_truth"
    description = (
        "Search the ground truth dataset for relevant reference examples that have "
        "been evaluated by human experts. Returns similar cases with their expected "
        "outputs and scoring. Use this to find calibration examples when evaluating "
        "similar inputs."
    )
    category = "web"
    input_model = GroundTruthSearchInput

    def execute(
        self, params: GroundTruthSearchInput, context: ToolContext
    ) -> ToolResult:
        ground_truth_id = str(params.ground_truth_id or "").strip()
        if not ground_truth_id or ground_truth_id.lower() in {"default", "sample"}:
            return _ground_truth_candidates_result(
                context,
                "Ground Truth Required",
                f"Ground truth `{ground_truth_id or '(empty)'}` is not a valid dataset ID.",
            )
        try:
            from model_hub.utils.ground_truth_retrieval import (
                generate_embedding,
                retrieve_similar_examples,
            )

            query_embedding = generate_embedding(params.query)

            results = retrieve_similar_examples(
                ground_truth_id=ground_truth_id,
                query_embedding=query_embedding,
                max_examples=params.max_results,
                similarity_threshold=0.3,  # Lower threshold — let agent judge relevance
            )

            if not results:
                return ToolResult(
                    content="No relevant ground truth examples found for this query.",
                    data={
                        "query": params.query,
                        "ground_truth_id": ground_truth_id,
                        "total_results": 0,
                        "results": [],
                    },
                )

            # Format results for the agent
            formatted_parts = [
                f"Found {len(results)} relevant ground truth examples:\n"
            ]
            for i, result in enumerate(results, 1):
                formatted_parts.append(
                    f"--- Example {i} (similarity: {result['similarity']}) ---"
                )
                row_data = result["row_data"]
                for key, value in row_data.items():
                    val_str = str(value)
                    if len(val_str) > 500:
                        val_str = val_str[:500] + "..."
                    formatted_parts.append(f"  {key}: {val_str}")
                formatted_parts.append("")

            return ToolResult(
                content="\n".join(formatted_parts),
                data={
                    "query": params.query,
                    "ground_truth_id": ground_truth_id,
                    "total_results": len(results),
                    "results": results,
                },
            )

        except Exception as e:
            logger.error("ground_truth_search_failed", error=str(e))
            return _ground_truth_candidates_result(
                context,
                "Ground Truth Search Blocked",
                f"Ground truth search failed for `{ground_truth_id}`: {e}",
            )

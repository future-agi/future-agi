from enum import Enum

from pydantic import BaseModel as PydanticBaseModel

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool


class TaxonomyCategory(str, Enum):
    eval_types = "eval_types"
    dataset_sources = "dataset_sources"
    trace_span_types = "trace_span_types"
    model_types = "model_types"


class ReadTaxonomyInput(PydanticBaseModel):
    category: TaxonomyCategory | None = None


@register_tool
class ReadTaxonomyTool(BaseTool):
    name = "read_taxonomy"
    description = (
        "Returns available categories, types, and enums for a given domain. "
        "Use this to understand what options exist before querying or creating entities."
    )
    category = "context"
    input_model = ReadTaxonomyInput

    def execute(self, params: ReadTaxonomyInput, context: ToolContext) -> ToolResult:
        taxonomy_map = {
            TaxonomyCategory.eval_types: self._eval_types,
            TaxonomyCategory.dataset_sources: self._dataset_sources,
            TaxonomyCategory.trace_span_types: self._trace_span_types,
            TaxonomyCategory.model_types: self._model_types,
        }

        if params.category is None:
            rows = [
                [category.value, builder.__name__.removeprefix("_").replace("_", " ")]
                for category, builder in taxonomy_map.items()
            ]
            return ToolResult(
                content=section(
                    "Available Taxonomy Categories",
                    markdown_table(["Category", "Description"], rows),
                ),
                data={"categories": [category.value for category in taxonomy_map]},
            )

        builder = taxonomy_map.get(params.category)
        if not builder:
            return ToolResult.error(
                f"Unknown taxonomy category: {params.category}",
                error_code="VALIDATION_ERROR",
            )

        return builder(context)

    def _eval_types(self, context: ToolContext) -> ToolResult:
        try:
            from model_hub.models.evals_metric import EvalTemplate

            templates = (
                EvalTemplate.objects.filter(
                    organization=context.organization,
                    workspace=context.workspace,
                )
                .values_list("name", "description", "owner", "eval_tags")
                .order_by("name")[:50]
            )

            rows = []
            for name, desc, owner, tags in templates:
                tag_str = ", ".join(tags) if tags else "—"
                rows.append([name, owner, desc[:100] if desc else "—", tag_str])

            if not rows:
                return ToolResult(
                    content="## Evaluation Types\n\n_No evaluation templates found in this workspace._"
                )

            table = markdown_table(["Name", "Owner", "Description", "Tags"], rows)
            return ToolResult(content=section(f"Evaluation Types ({len(rows)})", table))
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            return ToolResult.error(
                f"Failed to fetch eval types: {e}",
                error_code=code_from_exception(e),
            )

    def _dataset_sources(self, context: ToolContext) -> ToolResult:
        sources = [
            ["BUILD", "Created via the dataset builder UI"],
            ["UPLOAD", "Uploaded from a CSV/JSON file"],
            ["API", "Created via API or SDK"],
            ["SYNTHETIC", "Generated synthetically by AI"],
        ]
        table = markdown_table(["Source", "Description"], sources)
        return ToolResult(content=section("Dataset Sources", table))

    def _trace_span_types(self, context: ToolContext) -> ToolResult:
        span_types = [
            ["tool", "Tool/function call execution"],
            ["chain", "Chain of operations"],
            ["llm", "LLM API call"],
            ["retriever", "Document retrieval operation"],
            ["embedding", "Embedding generation"],
            ["agent", "Agent execution"],
            ["reranker", "Reranking operation"],
            ["guardrail", "Guardrail check"],
            ["evaluator", "Evaluation check"],
            ["conversation", "Conversation turn"],
            ["unknown", "Unclassified span"],
        ]
        table = markdown_table(["Type", "Description"], span_types)
        return ToolResult(content=section("Trace Span Types", table))

    def _model_types(self, context: ToolContext) -> ToolResult:
        model_types = [
            ["GENERATIVE_LLM", "Large Language Model (text generation)"],
            ["GENERATIVE_IMAGE", "Image generation model"],
            ["GENERATIVE_VIDEO", "Video generation model"],
            ["MULTI_MODAL", "Multi-modal model"],
            ["TTS", "Text-to-speech model"],
            ["STT", "Speech-to-text model"],
            ["RANKING", "Ranking model"],
            ["BINARY_CF", "Binary classification model"],
            ["REGRESSION", "Regression model"],
            ["OBJECT_DETECTION", "Object detection model"],
            ["SEGMENTATION", "Segmentation model"],
        ]
        table = markdown_table(["Type", "Description"], model_types)
        return ToolResult(content=section("Model Types", table))

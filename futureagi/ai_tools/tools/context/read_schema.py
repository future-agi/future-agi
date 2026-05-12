from enum import Enum
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool


class EntityType(str, Enum):
    evaluations = "evaluations"
    datasets = "datasets"
    traces = "traces"


class ReadSchemaInput(PydanticBaseModel):
    entity_type: EntityType | None = None


@register_tool
class ReadSchemaTool(BaseTool):
    name = "read_schema"
    description = (
        "Returns the database schema for a given entity type. "
        "Shows available fields, their types, and filter options. "
        "Use this to understand what data you can query."
    )
    category = "context"
    input_model = ReadSchemaInput

    def execute(self, params: ReadSchemaInput, context: ToolContext) -> ToolResult:
        schema_map = {
            EntityType.evaluations: self._evaluation_schema,
            EntityType.datasets: self._dataset_schema,
            EntityType.traces: self._trace_schema,
        }

        if params.entity_type is None:
            rows = [
                [entity_type.value, builder.__name__.removeprefix("_").replace("_", " ")]
                for entity_type, builder in schema_map.items()
            ]
            return ToolResult(
                content=section(
                    "Available Schema Entity Types",
                    markdown_table(["Entity Type", "Description"], rows),
                ),
                data={"entity_types": [entity_type.value for entity_type in schema_map]},
            )

        builder = schema_map.get(params.entity_type)
        if not builder:
            return ToolResult.error(
                f"Unknown entity type: {params.entity_type}",
                error_code="VALIDATION_ERROR",
            )

        return builder(context)

    def _evaluation_schema(self, context: ToolContext) -> ToolResult:
        fields = [
            ["id", "UUID", "Primary key"],
            ["eval_template", "ForeignKey", "Evaluation template used"],
            ["model_name", "String", "Model used for evaluation"],
            ["status", "Enum", "pending, processing, completed, failed"],
            ["value", "Text", "Formatted output value"],
            ["output_type", "String", "Output type (Pass/Fail, score, choices)"],
            ["output_bool", "Boolean", "Boolean result (for pass/fail)"],
            ["output_float", "Float", "Numeric result (for scores)"],
            ["reason", "Text", "Explanation of result"],
            ["runtime", "Float", "Runtime in seconds"],
            ["metrics", "JSON", "Evaluation metrics"],
            ["metadata", "JSON", "Additional metadata"],
            ["error_message", "Text", "Error details if failed"],
            ["created_at", "DateTime", "When evaluation was created"],
        ]
        table = markdown_table(["Field", "Type", "Description"], fields)

        # Get available eval templates in this workspace
        templates_info = self._get_eval_templates(context)

        content = section("Evaluation Schema", table)
        if templates_info:
            content += f"\n\n### Available Eval Templates\n\n{templates_info}"

        return ToolResult(content=content)

    def _dataset_schema(self, context: ToolContext) -> ToolResult:
        fields = [
            ["id", "UUID", "Primary key"],
            ["name", "String", "Dataset name"],
            ["source", "Enum", "BUILD, UPLOAD, API, SYNTHETIC"],
            ["model_type", "Enum", "Model type classification"],
            ["column_order", "Array", "Ordered column names"],
            ["column_config", "JSON", "Column configuration"],
            ["dataset_config", "JSON", "Dataset configuration"],
            ["created_at", "DateTime", "When dataset was created"],
        ]
        table = markdown_table(["Field", "Type", "Description"], fields)
        content = section("Dataset Schema", table)

        # Column sub-schema
        col_fields = [
            ["id", "UUID", "Primary key"],
            ["name", "String", "Column name"],
            ["data_type", "Enum", "Column data type"],
            ["source", "Enum", "Column source"],
            ["metadata", "JSON", "Column metadata"],
        ]
        col_table = markdown_table(["Field", "Type", "Description"], col_fields)
        content += f"\n\n### Column Schema\n\n{col_table}"

        return ToolResult(content=content)

    def _trace_schema(self, context: ToolContext) -> ToolResult:
        fields = [
            ["id", "UUID", "Primary key"],
            ["project", "ForeignKey", "Project this trace belongs to"],
            ["name", "String", "Trace name"],
            ["metadata", "JSON", "Trace metadata"],
            ["input", "JSON", "Trace input data"],
            ["output", "JSON", "Trace output data"],
            ["error", "JSON", "Error information if any"],
            ["tags", "Array", "Tags for filtering"],
            ["external_id", "String", "External trace ID"],
            ["created_at", "DateTime", "When trace was created"],
        ]
        table = markdown_table(["Field", "Type", "Description"], fields)

        # Span sub-schema
        span_fields = [
            ["id", "String", "Span identifier"],
            ["name", "String", "Span name"],
            [
                "observation_type",
                "Enum",
                "tool, chain, llm, retriever, embedding, agent, reranker, unknown, guardrail, evaluator, conversation",
            ],
            ["model", "String", "Model used (for LLM spans)"],
            ["latency_ms", "Integer", "Latency in milliseconds"],
            ["prompt_tokens", "Integer", "Input token count"],
            ["completion_tokens", "Integer", "Output token count"],
            ["total_tokens", "Integer", "Total token count"],
            ["cost", "Float", "Cost of this span"],
            ["status", "Enum", "UNSET, OK, ERROR"],
            ["provider", "String", "LLM provider name"],
            ["start_time", "DateTime", "When span started"],
            ["end_time", "DateTime", "When span ended"],
        ]
        span_table = markdown_table(["Field", "Type", "Description"], span_fields)

        content = section("Trace Schema", table)
        content += f"\n\n### Span Schema\n\n{span_table}"

        return ToolResult(content=content)

    def _get_eval_templates(self, context: ToolContext) -> Optional[str]:
        try:
            from model_hub.models.evals_metric import EvalTemplate

            templates = EvalTemplate.objects.filter(
                organization=context.organization,
                workspace=context.workspace,
            ).values_list("id", "name", "owner", "eval_tags")[:20]

            if not templates:
                return None

            rows = []
            for t_id, name, owner, tags in templates:
                tag_str = ", ".join(tags) if tags else "—"
                rows.append([f"`{str(t_id)}`", name, owner, tag_str])

            return markdown_table(["ID", "Name", "Owner", "Tags"], rows)
        except Exception:
            return None

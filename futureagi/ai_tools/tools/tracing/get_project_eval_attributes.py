from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from django.db import connection

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import markdown_table, section
from ai_tools.registry import register_tool


class GetProjectEvalAttributesInput(PydanticBaseModel):
    project_id: UUID = Field(
        description="The UUID of the project to fetch eval attributes for"
    )


def _attribute_key(attribute) -> str | None:
    if isinstance(attribute, dict):
        key = attribute.get("key") or attribute.get("name")
        return str(key) if key else None
    return str(attribute) if attribute else None


def _get_sampled_pg_span_attributes(project_id: str) -> list[str]:
    query = """
        WITH sampled_spans AS (
            SELECT span_attributes, eval_attributes
            FROM tracer_observation_span
            WHERE project_id = %s
              AND deleted = FALSE
              AND (
                span_attributes IS NOT NULL AND span_attributes != '{}'::jsonb
                OR eval_attributes IS NOT NULL AND eval_attributes != '{}'::jsonb
              )
            ORDER BY created_at DESC
            LIMIT 2000
        )
        SELECT DISTINCT key
        FROM sampled_spans,
        LATERAL jsonb_object_keys(
            COALESCE(NULLIF(span_attributes, '{}'::jsonb), eval_attributes)
        ) AS key
        ORDER BY key
        LIMIT 1000
    """
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = %s", [8000])
        try:
            cursor.execute(query, [project_id])
            rows = cursor.fetchall()
        finally:
            cursor.execute("SET statement_timeout = DEFAULT")
    return [row[0] for row in rows]


@register_tool
class GetProjectEvalAttributesTool(BaseTool):
    name = "get_project_eval_attributes"
    description = (
        "Returns the list of all available span/eval attribute keys for a project. "
        "These are the valid values that can be used in the 'mapping' field when "
        "creating a custom eval config. Use this to discover what attribute keys "
        "exist in the project's spans before configuring eval mappings."
    )
    category = "tracing"
    input_model = GetProjectEvalAttributesInput

    def execute(
        self, params: GetProjectEvalAttributesInput, context: ToolContext
    ) -> ToolResult:

        from tracer.models.project import Project
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
            QueryType,
        )

        # Validate project
        try:
            project = Project.objects.get(
                id=params.project_id, organization=context.organization
            )
        except Project.DoesNotExist:
            return ToolResult.not_found("Project", str(params.project_id))

        # Prefer the bounded ClickHouse path used by the product endpoint. The
        # PostgreSQL fallback samples recent spans so one wide project cannot
        # consume the full Falcon tool timeout.
        attributes: list[str] = []
        backend = "postgres"
        try:
            analytics = AnalyticsQueryService()
            if analytics.should_use_clickhouse(QueryType.SPAN_LIST):
                ch_attributes = analytics.get_span_attribute_keys_ch(
                    str(params.project_id)
                )
                attributes = [
                    key
                    for key in (_attribute_key(attr) for attr in ch_attributes)
                    if key
                ]
                if attributes:
                    backend = "clickhouse"
        except Exception:
            attributes = []

        if not attributes:
            attributes = _get_sampled_pg_span_attributes(str(params.project_id))

        if not attributes:
            return ToolResult(
                content=section(
                    f"Eval Attributes: {project.name}",
                    "_No span attributes found for this project. "
                    "Ensure the project has traces with span attributes._",
                ),
                data={
                    "attributes": [],
                    "project_id": str(params.project_id),
                    "backend": backend,
                },
            )

        sorted_attributes = sorted(set(attributes))

        rows = [[attr] for attr in sorted_attributes]
        table = markdown_table(["Attribute Key"], rows)

        content = section(
            f"Eval Attributes: {project.name} ({len(sorted_attributes)})",
            f"These are the available attribute keys that can be used in eval config mappings.\n\n{table}",
        )

        return ToolResult(
            content=content,
            data={
                "attributes": sorted_attributes,
                "project_id": str(params.project_id),
                "backend": backend,
            },
        )

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class SearchInput(PydanticBaseModel):
    query: str = Field(description="Search query (case-insensitive)")
    entity_types: list[str] | None = Field(
        default=None,
        description=(
            "Entity types to search. Options: evaluations, datasets, traces, "
            "agents, experiments, projects, prompt_templates. Defaults to all."
        ),
    )
    limit: int = Field(
        default=10, ge=1, le=50, description="Max results per entity type"
    )


def _in_caller_scope(
    queryset, context, organization_field="organization", workspace_field="workspace"
):
    """Rows of the caller's organization visible from its workspace, by the
    managers' rule; no organization means no rows."""
    from tfc.utils.tenant_scope import workspace_scope_q

    organization = getattr(context, "organization", None)
    if organization is None:
        return queryset.none()
    return queryset.filter(
        workspace_scope_q(getattr(context, "workspace", None), workspace_field),
        **{organization_field: organization},
    )


ALL_ENTITY_TYPES = [
    "evaluations",
    "datasets",
    "traces",
    "agents",
    "experiments",
    "projects",
    "prompt_templates",
]


@register_tool
class SearchTool(BaseTool):
    name = "search"
    description = (
        "Unified search across all entity types in the current workspace. "
        "Searches by name/title using case-insensitive matching. "
        "Returns results grouped by entity type."
    )
    category = "context"
    input_model = SearchInput

    def execute(self, params: SearchInput, context: ToolContext) -> ToolResult:

        types = params.entity_types or ALL_ENTITY_TYPES
        results = {}
        total_found = 0

        for entity_type in types:
            handler = getattr(self, f"_search_{entity_type}", None)
            if handler:
                items = handler(params.query, params.limit, context)
                if items:
                    results[entity_type] = items
                    total_found += len(items)

        if not results:
            return ToolResult(
                content=f'## Search Results\n\nNo results found for **"{params.query}"** across {", ".join(types)}.',
                data={"query": params.query, "total": 0, "results": {}},
            )

        content = section(
            "Search Results",
            f'Found **{total_found}** results for **"{params.query}"**\n',
        )

        data_results = {}
        for entity_type, items in results.items():
            rows = [[i["link"], i["detail"], i["created"]] for i in items]
            table = markdown_table(["Name (ID)", "Detail", "Created"], rows)
            content += f"\n### {entity_type.title()} ({len(items)})\n\n{table}\n"
            data_results[entity_type] = [
                {"id": i["id"], "name": i["name"]} for i in items
            ]

        return ToolResult(
            content=content,
            data={"query": params.query, "total": total_found, "results": data_results},
        )

    def _search_evaluations(self, query: str, limit: int, context) -> list[dict]:
        from model_hub.models.evaluation import Evaluation

        qs = (
            _in_caller_scope(
                Evaluation.objects.select_related("eval_template"), context
            )
            .filter(eval_template__name__icontains=query)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(ev.id),
                "link": dashboard_link(
                    "evaluation",
                    str(ev.id),
                    label=f"{ev.eval_template.name if ev.eval_template else '—'} (`{ev.id}`)",
                ),
                "name": ev.eval_template.name if ev.eval_template else "—",
                "detail": f"Status: {ev.status}",
                "created": format_datetime(ev.created_at),
            }
            for ev in qs
        ]

    def _search_datasets(self, query: str, limit: int, context) -> list[dict]:
        from model_hub.models.develop_dataset import Dataset

        qs = (
            _in_caller_scope(Dataset.objects.all(), context)
            .filter(name__icontains=query)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(ds.id),
                "link": dashboard_link(
                    "dataset", str(ds.id), label=f"{truncate(ds.name, 40)} (`{ds.id}`)"
                ),
                "name": truncate(ds.name, 40),
                "detail": f"Source: {ds.source}",
                "created": format_datetime(ds.created_at),
            }
            for ds in qs
        ]

    def _search_traces(self, query: str, limit: int, context) -> list[dict]:
        from tracer.models.trace import Trace

        qs = (
            _in_caller_scope(
                Trace.objects.select_related("project"),
                context,
                organization_field="project__organization",
                workspace_field="project__workspace",
            )
            .filter(name__icontains=query)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(t.id),
                "link": dashboard_link(
                    "trace",
                    str(t.id),
                    label=f"{truncate(t.name, 40) if t.name else '—'} (`{t.id}`)",
                ),
                "name": truncate(t.name, 40) if t.name else "—",
                "detail": t.project.name if t.project else "—",
                "created": format_datetime(t.created_at),
            }
            for t in qs
        ]

    def _search_agents(self, query: str, limit: int, context) -> list[dict]:
        from simulate.models.agent_definition import AgentDefinition

        qs = (
            _in_caller_scope(AgentDefinition.objects.all(), context)
            .filter(agent_name__icontains=query)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(a.id),
                "link": dashboard_link(
                    "agent", str(a.id), label=f"{truncate(a.agent_name, 40)} (`{a.id}`)"
                ),
                "name": truncate(a.agent_name, 40),
                "detail": f"Type: {a.agent_type}",
                "created": format_datetime(a.created_at),
            }
            for a in qs
        ]

    def _search_experiments(self, query: str, limit: int, context) -> list[dict]:
        from model_hub.models.experiments import ExperimentsTable

        qs = (
            _in_caller_scope(
                ExperimentsTable.objects.all(),
                context,
                organization_field="dataset__organization",
                workspace_field="dataset__workspace",
            )
            .filter(name__icontains=query)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(e.id),
                "link": dashboard_link(
                    "experiment", str(e.id), label=f"{truncate(e.name, 40)} (`{e.id}`)"
                ),
                "name": truncate(e.name, 40),
                "detail": f"Status: {e.status}",
                "created": format_datetime(e.created_at),
            }
            for e in qs
        ]

    def _search_projects(self, query: str, limit: int, context) -> list[dict]:
        from tracer.models.project import Project

        qs = (
            _in_caller_scope(Project.objects.all(), context)
            .filter(name__icontains=query)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(p.id),
                "link": dashboard_link(
                    "project", str(p.id), label=f"{truncate(p.name, 40)} (`{p.id}`)"
                ),
                "name": truncate(p.name, 40),
                "detail": f"Type: {p.trace_type}",
                "created": format_datetime(p.created_at),
            }
            for p in qs
        ]

    def _search_prompt_templates(self, query: str, limit: int, context) -> list[dict]:
        from model_hub.models.run_prompt import PromptTemplate

        qs = (
            _in_caller_scope(PromptTemplate.objects.all(), context)
            .filter(name__icontains=query, deleted=False)
            .order_by("-created_at")[:limit]
        )
        return [
            {
                "id": str(pt.id),
                "link": dashboard_link(
                    "prompt_template",
                    str(pt.id),
                    label=f"{truncate(pt.name, 40)} (`{pt.id}`)",
                ),
                "name": truncate(pt.name, 40),
                "detail": f"Versions: {pt.all_executions.filter(deleted=False).count()}",
                "created": format_datetime(pt.created_at),
            }
            for pt in qs
        ]

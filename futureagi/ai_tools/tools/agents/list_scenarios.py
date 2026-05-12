from typing import Optional
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_status,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool


class ListScenariosInput(PydanticBaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    agent_id: Optional[UUID] = Field(
        default=None, description="Filter by agent definition ID"
    )
    agent_ref: Optional[str] = Field(
        default=None, description="Filter by agent ID or agent name"
    )
    scenario_type: Optional[str] = Field(
        default=None, description="Filter by type: graph, script, dataset"
    )
    search: Optional[str] = Field(
        default=None,
        description="Search scenarios by name, source, or type",
    )


@register_tool
class ListScenariosTool(BaseTool):
    name = "list_scenarios"
    description = (
        "Lists test scenarios for agent testing. Scenarios define "
        "the conversation flow and test cases. Types: graph (visual flow), "
        "script (text-based), dataset (data-driven)."
    )
    category = "agents"
    input_model = ListScenariosInput

    def execute(self, params: ListScenariosInput, context: ToolContext) -> ToolResult:

        from django.db.models import Q

        from ai_tools.tools.agents._utils import resolve_agent
        from simulate.models.scenarios import Scenarios

        qs = (
            Scenarios.objects.select_related("agent_definition")
            .filter(
                organization=context.organization,
                deleted=False,
            )
            .order_by("-created_at")
        )

        agent_filter = None
        if params.agent_ref:
            agent_filter, error = resolve_agent(
                params.agent_ref,
                context,
                title="Agent Needed To List Scenarios",
            )
            if error:
                return error
        if params.agent_id:
            qs = qs.filter(agent_definition_id=params.agent_id)
        elif agent_filter:
            qs = qs.filter(agent_definition=agent_filter)
        if params.scenario_type:
            qs = qs.filter(scenario_type=params.scenario_type)
        if params.search:
            search = params.search.strip()
            if search:
                qs = qs.filter(
                    Q(name__icontains=search)
                    | Q(source__icontains=search)
                    | Q(scenario_type__icontains=search)
                )

        total = qs.count()
        scenarios = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for s in scenarios:
            agent_name = s.agent_definition.agent_name if s.agent_definition else "—"

            rows.append(
                [
                    f"`{s.id}`",
                    truncate(s.name, 35),
                    s.scenario_type or "—",
                    agent_name,
                    s.source_type or "—",
                    (
                        format_status(s.status)
                        if hasattr(s, "status") and s.status
                        else "—"
                    ),
                    format_datetime(s.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(s.id),
                    "name": s.name,
                    "type": s.scenario_type,
                    "agent": agent_name,
                    "source_type": s.source_type,
                }
            )

        table = markdown_table(
            ["ID", "Name", "Type", "Agent", "Source", "Status", "Created"],
            rows,
        )

        showing = f"Showing {len(rows)} of {total}"
        if params.agent_id or agent_filter:
            showing += f" (agent: {params.agent_id or agent_filter.id})"

        content = section(f"Scenarios ({total})", f"{showing}\n\n{table}")

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(
            content=content, data={"scenarios": data_list, "total": total}
        )

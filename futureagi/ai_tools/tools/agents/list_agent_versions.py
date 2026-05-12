from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    format_number,
    format_status,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_agent


class ListAgentVersionsInput(PydanticBaseModel):
    agent_id: Optional[str] = Field(
        default=None,
        description="The UUID or exact name of the agent definition",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


@register_tool
class ListAgentVersionsTool(BaseTool):
    name = "list_agent_versions"
    description = (
        "Lists version history for an agent. Each version captures "
        "a configuration snapshot with performance metrics (score, test count, pass rate)."
    )
    category = "agents"
    input_model = ListAgentVersionsInput

    def execute(
        self, params: ListAgentVersionsInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.agent_version import AgentVersion

        agent, unresolved = resolve_agent(
            params.agent_id,
            context,
            title="Agent Required For Version History",
        )
        if unresolved:
            return unresolved

        qs = AgentVersion.objects.filter(agent_definition=agent).order_by(
            "-version_number"
        )

        total = qs.count()
        versions = qs[params.offset : params.offset + params.limit]

        rows = []
        data_list = []
        for v in versions:
            score = format_number(v.score) if v.score is not None else "—"
            pass_rate = f"{v.pass_rate}%" if v.pass_rate is not None else "—"

            rows.append(
                [
                    f"`{v.id}`",
                    v.version_name or f"v{v.version_number}",
                    format_status(v.status),
                    score,
                    str(v.test_count),
                    pass_rate,
                    truncate(v.commit_message, 40) if v.commit_message else "—",
                    format_datetime(v.created_at),
                ]
            )
            data_list.append(
                {
                    "id": str(v.id),
                    "version_number": v.version_number,
                    "version_name": v.version_name,
                    "status": v.status,
                    "score": float(v.score) if v.score is not None else None,
                    "test_count": v.test_count,
                    "pass_rate": (
                        float(v.pass_rate) if v.pass_rate is not None else None
                    ),
                }
            )

        table = markdown_table(
            [
                "ID",
                "Version",
                "Status",
                "Score",
                "Tests",
                "Pass Rate",
                "Commit Message",
                "Created",
            ],
            rows,
        )

        content = section(
            f"Agent Versions: {agent.agent_name} ({total})",
            f"Showing {len(rows)} of {total}\n\n{table}",
        )

        if total > params.offset + params.limit:
            content += (
                f"\n\n_Use offset={params.offset + params.limit} to see more results._"
            )

        return ToolResult(content=content, data={"versions": data_list, "total": total})

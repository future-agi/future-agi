from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import candidate_agents_result, resolve_agent


class GetAgentInput(PydanticBaseModel):
    agent_id: Optional[str] = Field(
        default=None,
        description=(
            "Agent UUID or exact agent name to retrieve. Omit to list candidate "
            "agents with IDs."
        ),
    )
    include_test_history: bool = Field(
        default=True, description="Include recent test execution history"
    )


@register_tool
class GetAgentTool(BaseTool):
    name = "get_agent"
    description = (
        "Returns detailed information about a specific agent definition, "
        "including its configuration, versions, and recent test history."
    )
    category = "agents"
    input_model = GetAgentInput

    def execute(self, params: GetAgentInput, context: ToolContext) -> ToolResult:
        if not params.agent_id:
            return candidate_agents_result(context)

        agent, unresolved = resolve_agent(
            params.agent_id,
            context,
            title="Agent Required",
        )
        if unresolved:
            return unresolved

        info = key_value_block(
            [
                ("ID", f"`{agent.id}`"),
                ("Name", agent.agent_name),
                ("Type", agent.agent_type),
                ("Provider", agent.provider or "—"),
                ("Model", agent.model or "—"),
                ("Language", agent.language or "—"),
                ("Inbound", "Yes" if agent.inbound else "No"),
                (
                    "Description",
                    truncate(agent.description, 200) if agent.description else "—",
                ),
                ("Created", format_datetime(agent.created_at)),
                (
                    "Link",
                    dashboard_link("agent", str(agent.id), label="View in Dashboard"),
                ),
            ]
        )
        content = section(f"Agent: {agent.agent_name}", info)

        # Version information
        from simulate.models.agent_version import AgentVersion

        active_version = agent.active_version
        if active_version:
            content += f"\n\n### Active Version\n\n"
            content += key_value_block(
                [
                    ("Version", f"v{active_version.version_number}"),
                    ("Status", active_version.status),
                    (
                        "Description",
                        (
                            truncate(active_version.description, 100)
                            if active_version.description
                            else "—"
                        ),
                    ),
                    ("Created", format_datetime(active_version.created_at)),
                ]
            )

        # Test history
        test_data = []
        if params.include_test_history:
            from simulate.models.run_test import RunTest
            from simulate.models.test_execution import TestExecution

            run_tests = RunTest.objects.filter(agent_definition=agent).order_by(
                "-created_at"
            )[:5]

            if run_tests.exists():
                test_rows = []
                for rt in run_tests:
                    latest_exec = (
                        TestExecution.objects.filter(run_test=rt)
                        .order_by("-created_at")
                        .first()
                    )
                    status = latest_exec.status if latest_exec else "—"
                    total_calls = latest_exec.total_calls if latest_exec else 0
                    completed = latest_exec.completed_calls if latest_exec else 0
                    failed = latest_exec.failed_calls if latest_exec else 0

                    test_rows.append(
                        [
                            truncate(rt.name, 30),
                            status,
                            str(total_calls),
                            str(completed),
                            str(failed),
                            format_datetime(rt.created_at),
                        ]
                    )
                    test_data.append(
                        {
                            "run_test_id": str(rt.id),
                            "name": rt.name,
                            "status": status,
                            "total_calls": total_calls,
                            "completed": completed,
                            "failed": failed,
                        }
                    )

                test_table = markdown_table(
                    ["Test", "Status", "Total", "Completed", "Failed", "Created"],
                    test_rows,
                )
                content += f"\n\n### Recent Tests ({len(test_rows)})\n\n{test_table}"
            else:
                content += "\n\n### Recent Tests\n\n_No tests found for this agent._"

        data = {
            "id": str(agent.id),
            "name": agent.agent_name,
            "type": agent.agent_type,
            "provider": agent.provider,
            "model": agent.model,
            "language": agent.language,
            "tests": test_data,
            "active_version": (
                {
                    "id": str(active_version.id),
                    "version_number": active_version.version_number,
                    "status": active_version.status,
                }
                if active_version
                else None
            ),
            "version_count": agent.version_count,
        }

        return ToolResult(content=content, data=data)

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.agents._utils import resolve_agent
from simulate.models import AgentVersion


class DeleteAgentDefinitionInput(PydanticBaseModel):
    agent_id: str = Field(
        default="",
        description="Agent definition name or UUID to delete. If omitted, candidates are returned.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms deletion.",
    )


@register_tool
class DeleteAgentDefinitionTool(BaseTool):
    name = "delete_agent_definition"
    description = (
        "Soft-deletes an agent definition by marking it as deleted. "
        "The agent and its data are preserved but hidden from queries."
    )
    category = "simulation"
    input_model = DeleteAgentDefinitionInput

    def execute(
        self, params: DeleteAgentDefinitionInput, context: ToolContext
    ) -> ToolResult:
        agent, unresolved = resolve_agent(
            params.agent_id,
            context,
            title="Agent Required To Delete",
        )
        if unresolved:
            return unresolved

        if not params.confirm_delete:
            info = key_value_block(
                [
                    ("ID", f"`{agent.id}`"),
                    ("Name", agent.agent_name),
                    ("Status", "Awaiting confirmation"),
                ]
            )
            return ToolResult(
                content=section("Confirm Agent Deletion", info),
                data={
                    "requires_confirmation": True,
                    "confirm_delete": True,
                    "id": str(agent.id),
                    "name": agent.agent_name,
                },
            )

        with transaction.atomic():
            agent_name = agent.agent_name
            agent.delete()  # Uses BaseModel.delete() soft delete

            AgentVersion.objects.filter(
                agent_definition=agent,
                organization=agent.organization,
            ).update(deleted=True, deleted_at=timezone.now())

        info = key_value_block(
            [
                ("ID", f"`{agent.id}`"),
                ("Name", agent_name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Agent Deleted", info)

        return ToolResult(
            content=content,
            data={"id": str(agent.id), "name": agent_name, "deleted": True},
        )

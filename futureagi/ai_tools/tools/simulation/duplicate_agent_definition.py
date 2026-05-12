from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class DuplicateAgentDefinitionInput(PydanticBaseModel):
    agent_id: str = Field(
        default="",
        description="Agent definition name or UUID to duplicate",
    )
    new_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Name for the duplicated agent",
    )


@register_tool
class DuplicateAgentDefinitionTool(BaseTool):
    name = "duplicate_agent_definition"
    description = (
        "Creates a copy of an existing agent definition with a new name. "
        "All configuration is cloned except ID and timestamps."
    )
    category = "simulation"
    input_model = DuplicateAgentDefinitionInput

    def execute(
        self, params: DuplicateAgentDefinitionInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.agent_definition import AgentDefinition
        from simulate.models.agent_version import AgentVersion

        from ai_tools.tools.agents._utils import resolve_agent

        original, unresolved = resolve_agent(
            params.agent_id,
            context,
            title="Agent Required To Duplicate",
        )
        if unresolved:
            return unresolved

        new_name = params.new_name or f"{original.agent_name} Copy"
        if AgentDefinition.objects.filter(
            organization=context.organization,
            agent_name=new_name,
            deleted=False,
        ).exists():
            return ToolResult.validation_error(
                f"An agent named `{new_name}` already exists. Provide `new_name`."
            )

        clone = AgentDefinition(
            agent_name=new_name,
            agent_type=original.agent_type,
            contact_number=original.contact_number,
            inbound=original.inbound,
            description=original.description,
            provider=original.provider,
            language=original.language,
            languages=original.languages,
            model=original.model,
            model_details=original.model_details,
            websocket_url=original.websocket_url,
            websocket_headers=original.websocket_headers,
            api_key=original.api_key,
            assistant_id=original.assistant_id,
            authentication_method=original.authentication_method,
            knowledge_base=original.knowledge_base,
            organization=context.organization,
            workspace=context.workspace,
        )
        clone.save()

        # Create initial version for the clone (matching create behavior)
        clone.create_version(
            description=clone.description or "",
            commit_message=f"Duplicated from {original.agent_name}",
            status=AgentVersion.StatusChoices.ACTIVE,
        )

        info = key_value_block(
            [
                ("New ID", f"`{clone.id}`"),
                ("New Name", clone.agent_name),
                ("Cloned From", f"`{original.id}` ({original.agent_name})"),
                ("Type", clone.agent_type),
                ("Provider", clone.provider or "—"),
                ("Model", clone.model or "—"),
                ("Created", format_datetime(clone.created_at)),
            ]
        )

        content = section("Agent Duplicated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(clone.id),
                "name": clone.agent_name,
                "cloned_from": str(original.id),
            },
        )

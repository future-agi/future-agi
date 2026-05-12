from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.resolvers import is_uuid


class UpdateSimulatorAgentInput(PydanticBaseModel):
    simulator_agent_id: str = Field(
        default="",
        description=(
            "Simulator agent UUID or exact/fuzzy name. Omit it to list candidates."
        ),
    )
    name: Optional[str] = Field(default=None, description="New name")
    prompt: Optional[str] = Field(default=None, description="New system prompt")
    voice_provider: Optional[str] = Field(
        default=None, description="New voice provider"
    )
    voice_name: Optional[str] = Field(default=None, description="New voice name")
    model: Optional[str] = Field(default=None, description="New LLM model")
    llm_temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="New temperature"
    )
    max_call_duration_in_minutes: Optional[int] = Field(
        default=None, ge=1, le=180, description="New max duration"
    )
    interrupt_sensitivity: Optional[float] = Field(
        default=None, ge=0.0, le=11.0, description="New interrupt sensitivity"
    )
    conversation_speed: Optional[float] = Field(
        default=None, ge=0.1, le=2.0, description="New conversation speed"
    )


@register_tool
class UpdateSimulatorAgentTool(BaseTool):
    name = "update_simulator_agent"
    description = "Updates an existing simulator agent configuration. Only provided fields will be changed."
    category = "simulation"
    input_model = UpdateSimulatorAgentInput

    def execute(
        self, params: UpdateSimulatorAgentInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.simulator_agent import SimulatorAgent

        def candidate_agents_result(title: str, detail: str = "") -> ToolResult:
            agents = list(
                SimulatorAgent.objects.filter(
                    organization=context.organization
                ).order_by("-created_at")[:10]
            )
            rows = []
            data = []
            for agent in agents:
                rows.append(
                    [
                        truncate(agent.name, 40),
                        f"`{agent.id}`",
                        agent.model or "—",
                        format_datetime(agent.created_at),
                    ]
                )
                data.append(
                    {
                        "id": str(agent.id),
                        "name": agent.name,
                        "model": agent.model,
                    }
                )
            body = detail or "Provide `simulator_agent_id` to update an agent."
            if rows:
                body += "\n\n" + markdown_table(
                    ["Name", "Agent ID", "Model", "Created"],
                    rows,
                )
            else:
                body += "\n\nNo simulator agents found in this workspace."
            return ToolResult.needs_input(
                section(title, body),
                data={"requires_simulator_agent_id": True, "simulator_agents": data},
                missing_fields=["simulator_agent_id"],
            )

        agent_ref = str(params.simulator_agent_id or "").strip()
        if not agent_ref:
            return candidate_agents_result("Simulator Agent Required")

        qs = SimulatorAgent.objects.filter(organization=context.organization)
        if is_uuid(agent_ref):
            sa = qs.filter(id=agent_ref).first()
        else:
            exact = list(qs.filter(name__iexact=agent_ref).order_by("-created_at")[:2])
            if len(exact) == 1:
                sa = exact[0]
            elif len(exact) > 1:
                return candidate_agents_result(
                    "Multiple Simulator Agents Matched",
                    f"More than one simulator agent matched `{agent_ref}`. Use an ID.",
                )
            else:
                fuzzy = list(
                    qs.filter(name__icontains=agent_ref).order_by("-created_at")[:2]
                )
                sa = fuzzy[0] if len(fuzzy) == 1 else None
        if not sa:
            return candidate_agents_result(
                "Simulator Agent Not Found",
                f"Simulator agent `{agent_ref}` was not found in this workspace.",
            )

        updated_fields = []
        field_map = {
            "name": params.name,
            "prompt": params.prompt,
            "voice_provider": params.voice_provider,
            "voice_name": params.voice_name,
            "model": params.model,
            "llm_temperature": params.llm_temperature,
            "max_call_duration_in_minutes": params.max_call_duration_in_minutes,
            "interrupt_sensitivity": params.interrupt_sensitivity,
            "conversation_speed": params.conversation_speed,
        }

        for field_name, value in field_map.items():
            if value is not None:
                setattr(sa, field_name, value)
                updated_fields.append(field_name)

        if not updated_fields:
            return ToolResult.error(
                "No fields provided to update.",
                error_code="VALIDATION_ERROR",
            )

        sa.save(update_fields=updated_fields + ["updated_at"])

        info = key_value_block(
            [
                ("ID", f"`{sa.id}`"),
                ("Name", sa.name),
                ("Updated Fields", ", ".join(updated_fields)),
                ("Updated At", format_datetime(sa.updated_at)),
            ]
        )

        content = section("Simulator Agent Updated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(sa.id),
                "name": sa.name,
                "updated_fields": updated_fields,
            },
        )

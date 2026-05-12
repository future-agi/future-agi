import uuid
from typing import Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool


class CreateSimulatorAgentInput(PydanticBaseModel):
    name: str = Field(
        default="",
        description="Name of the simulator agent. If omitted, a unique test-safe name is generated.",
    )
    prompt: str = Field(
        default="",
        description="System prompt for the simulator agent. If omitted, a safe default simulator prompt is used.",
    )
    voice_provider: Optional[str] = Field(
        default="openai", description="Voice service provider"
    )
    voice_name: Optional[str] = Field(
        default="alloy", description="Specific voice to use"
    )
    model: Optional[str] = Field(default="gpt-4o", description="LLM model to use")
    llm_temperature: Optional[float] = Field(
        default=0.7, ge=0.0, le=2.0, description="LLM temperature (0-2)"
    )
    max_call_duration_in_minutes: Optional[int] = Field(
        default=30, ge=1, le=180, description="Max call duration in minutes"
    )
    interrupt_sensitivity: Optional[float] = Field(
        default=0.5, ge=0.0, le=11.0, description="Interruption detection sensitivity"
    )
    conversation_speed: Optional[float] = Field(
        default=1.0, ge=0.1, le=2.0, description="Conversation speed"
    )


@register_tool
class CreateSimulatorAgentTool(BaseTool):
    name = "create_simulator_agent"
    description = (
        "Creates a new simulator agent (bot configuration) for test simulations. "
        "Configure voice, model, and conversation settings."
    )
    category = "simulation"
    input_model = CreateSimulatorAgentInput

    def execute(
        self, params: CreateSimulatorAgentInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.simulator_agent import SimulatorAgent

        name = params.name.strip() or f"falcon_simulator_agent_{uuid.uuid4().hex[:8]}"
        prompt = params.prompt.strip() or (
            "You are a realistic customer simulator for local Falcon regression tests. "
            "Stay in character, ask concise follow-up questions, and avoid using real customer data."
        )

        sa = SimulatorAgent(
            name=name,
            prompt=prompt,
            voice_provider=params.voice_provider or "openai",
            voice_name=params.voice_name or "alloy",
            model=params.model or "gpt-4o",
            llm_temperature=(
                params.llm_temperature if params.llm_temperature is not None else 0.7
            ),
            max_call_duration_in_minutes=params.max_call_duration_in_minutes or 30,
            interrupt_sensitivity=(
                params.interrupt_sensitivity
                if params.interrupt_sensitivity is not None
                else 0.5
            ),
            conversation_speed=(
                params.conversation_speed
                if params.conversation_speed is not None
                else 1.0
            ),
            organization=context.organization,
            workspace=context.workspace,
        )
        sa.save()

        info = key_value_block(
            [
                ("ID", f"`{sa.id}`"),
                ("Name", sa.name),
                ("Model", sa.model),
                ("Voice Provider", sa.voice_provider),
                ("Voice", sa.voice_name),
                ("Temperature", str(sa.llm_temperature)),
                ("Max Duration", f"{sa.max_call_duration_in_minutes}m"),
                ("Interrupt Sensitivity", str(sa.interrupt_sensitivity)),
                ("Conversation Speed", str(sa.conversation_speed)),
                ("Created", format_datetime(sa.created_at)),
            ]
        )

        content = section("Simulator Agent Created", info)

        return ToolResult(
            content=content,
            data={
                "id": str(sa.id),
                "name": sa.name,
                "model": sa.model,
                "voice_provider": sa.voice_provider,
                "voice_name": sa.voice_name,
            },
        )

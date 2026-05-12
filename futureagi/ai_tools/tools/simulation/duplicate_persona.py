from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.simulation.update_persona import _resolve_persona


class DuplicatePersonaInput(PydanticBaseModel):
    model_config = ConfigDict(extra="allow")

    persona_id: str = Field(
        default="",
        description="Editable persona name or UUID to duplicate. If omitted, candidates are returned.",
    )
    new_name: str = Field(
        default="",
        description="Name for the duplicated persona. Defaults to '<original> Copy'.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["persona_id"] = (
            normalized.get("persona_id")
            or normalized.get("persona")
            or normalized.get("source_persona_id")
            or normalized.get("id")
            or ""
        )
        normalized["new_name"] = (
            normalized.get("new_name")
            or normalized.get("name")
            or normalized.get("duplicate_name")
            or ""
        )
        return normalized


@register_tool
class DuplicatePersonaTool(BaseTool):
    name = "duplicate_persona"
    description = (
        "Creates a copy of an existing persona with a new name. "
        "All configuration is cloned. The clone is always a workspace persona."
    )
    category = "simulation"
    input_model = DuplicatePersonaInput

    def execute(
        self, params: DuplicatePersonaInput, context: ToolContext
    ) -> ToolResult:

        from simulate.models.persona import Persona

        original, unresolved = _resolve_persona(params.persona_id, context)
        if unresolved:
            return unresolved

        new_name = (params.new_name or "").strip() or f"{original.name} Copy"

        # Check for duplicate name (matches PersonaViewSet._duplicate_persona)
        if Persona.objects.filter(
            name__iexact=new_name,
            workspace=context.workspace,
            persona_type="workspace",
            deleted=False,
        ).exists():
            return ToolResult(
                content=section(
                    "Persona Name Already Exists",
                    (
                        f"A persona named `{new_name}` already exists in this "
                        "workspace. Provide `new_name` to create a distinct copy."
                    ),
                ),
                data={
                    "requires_new_name": True,
                    "persona_id": str(original.id),
                    "existing_name": new_name,
                },
            )

        clone = Persona(
            name=new_name,
            description=original.description,
            persona_type="workspace",
            simulation_type=original.simulation_type,
            gender=original.gender,
            age_group=original.age_group,
            occupation=original.occupation,
            location=original.location,
            personality=original.personality,
            communication_style=original.communication_style,
            multilingual=original.multilingual,
            languages=original.languages,
            accent=original.accent,
            conversation_speed=original.conversation_speed,
            background_sound=original.background_sound,
            finished_speaking_sensitivity=original.finished_speaking_sensitivity,
            interrupt_sensitivity=original.interrupt_sensitivity,
            keywords=original.keywords,
            metadata=original.metadata,
            additional_instruction=original.additional_instruction,
            tone=original.tone,
            verbosity=original.verbosity,
            punctuation=original.punctuation,
            slang_usage=original.slang_usage,
            typos_frequency=original.typos_frequency,
            regional_mix=original.regional_mix,
            emoji_usage=original.emoji_usage,
            organization=context.organization,
            workspace=context.workspace,
        )
        clone.save()

        info = key_value_block(
            [
                ("New ID", f"`{clone.id}`"),
                ("New Name", clone.name),
                ("Cloned From", f"`{original.id}` ({original.name})"),
                ("Type", "workspace"),
                ("Simulation Type", clone.simulation_type),
                ("Created", format_datetime(clone.created_at)),
            ]
        )

        content = section("Persona Duplicated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(clone.id),
                "name": clone.name,
                "cloned_from": str(original.id),
            },
        )

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    key_value_block,
    section,
)
from ai_tools.registry import register_tool
from ai_tools.tools.simulation.update_persona import _resolve_persona


class DeletePersonaInput(PydanticBaseModel):
    persona_id: str = Field(
        default="",
        description="Persona name or UUID to delete. If omitted, candidates are returned.",
    )
    confirm_delete: bool = Field(
        default=False,
        description="Set true only after the user confirms deletion.",
    )


@register_tool
class DeletePersonaTool(BaseTool):
    name = "delete_persona"
    description = "Deletes a workspace persona. System personas cannot be deleted."
    category = "simulation"
    input_model = DeletePersonaInput

    def execute(self, params: DeletePersonaInput, context: ToolContext) -> ToolResult:
        from django.utils import timezone

        persona, unresolved = _resolve_persona(params.persona_id, context)
        if unresolved:
            return unresolved

        if persona.persona_type == "system":
            return ToolResult.error(
                "System personas cannot be deleted. Only workspace personas can be removed.",
                error_code="PERMISSION_DENIED",
            )

        if not params.confirm_delete:
            info = key_value_block(
                [
                    ("ID", f"`{persona.id}`"),
                    ("Name", persona.name),
                    ("Status", "Awaiting confirmation"),
                ]
            )
            return ToolResult(
                content=section("Confirm Persona Deletion", info),
                data={
                    "requires_confirmation": True,
                    "confirm_delete": True,
                    "id": str(persona.id),
                    "name": persona.name,
                },
            )

        persona_name = persona.name
        persona.deleted = True
        persona.deleted_at = timezone.now()
        persona.save(update_fields=["deleted", "deleted_at", "updated_at"])

        info = key_value_block(
            [
                ("ID", f"`{persona.id}`"),
                ("Name", persona_name),
                ("Status", "Deleted"),
            ]
        )

        content = section("Persona Deleted", info)

        return ToolResult(
            content=content,
            data={"id": str(persona.id), "name": persona_name, "deleted": True},
        )

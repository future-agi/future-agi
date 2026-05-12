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
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


class GetPersonaInput(PydanticBaseModel):
    persona_id: str = Field(
        default="",
        description="Persona name or UUID to retrieve. If omitted, candidates are returned.",
    )


@register_tool
class GetPersonaTool(BaseTool):
    name = "get_persona"
    description = (
        "Returns detailed information about a specific persona, "
        "including demographics, behavioral traits, speech characteristics, "
        "and text settings."
    )
    category = "simulation"
    input_model = GetPersonaInput

    def execute(self, params: GetPersonaInput, context: ToolContext) -> ToolResult:
        from django.core.exceptions import ValidationError
        from django.db.models import Q
        from simulate.models.persona import Persona

        def candidate_personas_result(title: str, detail: str = "") -> ToolResult:
            qs = Persona.objects.filter(
                Q(persona_type="system") | Q(workspace=context.workspace)
            ).order_by("-created_at")
            personas = list(qs[:10])
            rows = [
                [
                    f"`{persona.id}`",
                    truncate(persona.name, 36),
                    persona.persona_type,
                    persona.simulation_type,
                ]
                for persona in personas
            ]
            body = detail or "Provide `persona_id` to inspect a persona."
            if rows:
                body += "\n\n" + markdown_table(
                    ["ID", "Name", "Type", "Simulation Type"],
                    rows,
                )
            else:
                body += "\n\nNo personas found in this workspace."
            return ToolResult(
                content=section(title, body),
                data={
                    "requires_persona_id": True,
                    "personas": [
                        {
                            "id": str(persona.id),
                            "name": persona.name,
                            "persona_type": persona.persona_type,
                        }
                        for persona in personas
                    ],
                },
            )

        ref = clean_ref(params.persona_id)
        if not ref:
            return candidate_personas_result("Persona Required")

        qs = Persona.objects.filter(
            Q(persona_type="system") | Q(workspace=context.workspace)
        )
        ref_uuid = uuid_text(ref)
        try:
            if ref_uuid:
                persona = qs.get(id=ref_uuid)
            else:
                exact = qs.filter(name__iexact=ref)
                if exact.count() == 1:
                    persona = exact.first()
                elif exact.count() > 1:
                    return candidate_personas_result(
                        "Multiple Personas Matched",
                        f"More than one persona matched `{ref}`. Use one of these IDs.",
                    )
                else:
                    fuzzy = qs.filter(name__icontains=ref)
                    if fuzzy.count() == 1:
                        persona = fuzzy.first()
                    else:
                        return candidate_personas_result(
                            "Persona Not Found",
                            f"Persona `{ref}` was not found.",
                        )
        except (Persona.DoesNotExist, ValidationError, ValueError, TypeError):
            return candidate_personas_result(
                "Persona Not Found",
                f"Persona `{ref}` was not found.",
            )

        def list_str(val):
            if val and isinstance(val, list):
                return ", ".join(str(v) for v in val)
            return "—"

        info = key_value_block(
            [
                ("ID", f"`{persona.id}`"),
                ("Name", persona.name),
                ("Persona Type", persona.persona_type),
                ("Simulation Type", persona.simulation_type),
                (
                    "Description",
                    truncate(persona.description, 300) if persona.description else "—",
                ),
                ("Created", format_datetime(persona.created_at)),
            ]
        )

        content = section(f"Persona: {persona.name}", info)

        # Demographics
        demographics = key_value_block(
            [
                ("Gender", list_str(persona.gender)),
                ("Age Group", list_str(persona.age_group)),
                ("Occupation", list_str(persona.occupation)),
                ("Location", list_str(persona.location)),
            ]
        )
        content += f"\n\n### Demographics\n\n{demographics}"

        # Behavioral Profile
        behavioral = key_value_block(
            [
                ("Personality", list_str(persona.personality)),
                ("Communication Style", list_str(persona.communication_style)),
                ("Tone", persona.tone or "—"),
                ("Verbosity", persona.verbosity or "—"),
            ]
        )
        content += f"\n\n### Behavioral Profile\n\n{behavioral}"

        # Speech Characteristics (voice-specific)
        if persona.simulation_type == "voice":
            speech = key_value_block(
                [
                    ("Languages", list_str(persona.languages)),
                    ("Accent", list_str(persona.accent)),
                    ("Conversation Speed", list_str(persona.conversation_speed)),
                    ("Multilingual", "Yes" if persona.multilingual else "No"),
                    ("Background Sound", "Yes" if persona.background_sound else "No"),
                    ("Interrupt Sensitivity", list_str(persona.interrupt_sensitivity)),
                    (
                        "Finished Speaking Sensitivity",
                        list_str(persona.finished_speaking_sensitivity),
                    ),
                ]
            )
            content += f"\n\n### Speech Characteristics\n\n{speech}"

        # Text Settings (text-specific)
        if persona.simulation_type == "text":
            text_settings = key_value_block(
                [
                    ("Punctuation", persona.punctuation or "—"),
                    ("Slang Usage", persona.slang_usage or "—"),
                    ("Typos Frequency", persona.typos_frequency or "—"),
                    ("Regional Mix", persona.regional_mix or "—"),
                    ("Emoji Usage", persona.emoji_usage or "—"),
                ]
            )
            content += f"\n\n### Text Settings\n\n{text_settings}"

        # Additional instructions
        if persona.additional_instruction:
            content += f"\n\n### Additional Instructions\n\n{truncate(persona.additional_instruction, 500)}"

        # Keywords
        if persona.keywords:
            content += f"\n\n### Keywords\n\n{list_str(persona.keywords)}"

        data = {
            "id": str(persona.id),
            "name": persona.name,
            "persona_type": persona.persona_type,
            "simulation_type": persona.simulation_type,
            "gender": persona.gender,
            "personality": persona.personality,
            "tone": persona.tone,
            "verbosity": persona.verbosity,
        }

        return ToolResult(content=content, data=data)

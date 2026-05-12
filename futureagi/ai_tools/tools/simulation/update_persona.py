from django.core.exceptions import ValidationError
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator

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
from ai_tools.tools.simulation.create_persona import (
    Accent,
    AgeGroup,
    CommunicationStyle,
    ConversationSpeed,
    EmojiUsage,
    Gender,
    Language,
    Location,
    Occupation,
    Personality,
    Punctuation,
    RegionalMix,
    SlangUsage,
    Tone,
    TypoFrequency,
    Verbosity,
)


def _candidate_personas_result(
    context: ToolContext,
    title: str = "Persona Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from simulate.models.persona import Persona

    qs = Persona.objects.filter(workspace=context.workspace).exclude(
        persona_type="system"
    )
    search = clean_ref(search)
    if search:
        qs = qs.filter(name__icontains=search)
    personas = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            f"`{persona.id}`",
            truncate(persona.name, 36),
            persona.simulation_type,
            format_datetime(persona.created_at),
        ]
        for persona in personas
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["ID", "Name", "Simulation Type", "Created"],
            rows,
        )
    else:
        body = body or "No editable personas found in this workspace."
    return ToolResult.needs_input(
        section(title, body),
        data={
            "personas": [
                {"id": str(persona.id), "name": persona.name} for persona in personas
            ],
        },
        missing_fields=["persona_id"],
    )


def _resolve_persona(
    persona_ref: str, context: ToolContext
) -> tuple[object | None, ToolResult | None]:
    from simulate.models.persona import Persona

    ref = clean_ref(persona_ref)
    if not ref:
        return None, _candidate_personas_result(context)

    qs = Persona.objects.filter(workspace=context.workspace).exclude(
        persona_type="system"
    )
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None
        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        if exact.count() > 1:
            return None, _candidate_personas_result(
                context,
                "Multiple Personas Matched",
                f"More than one persona matched `{ref}`. Use one of these IDs.",
                search=ref,
            )
        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (Persona.DoesNotExist, ValidationError, ValueError, TypeError):
        pass

    return None, _candidate_personas_result(
        context,
        "Persona Not Found",
        f"Persona `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )


class UpdatePersonaInput(PydanticBaseModel):
    persona_id: str = Field(
        default="",
        description="Editable persona name or UUID to update",
    )
    name: str | None = Field(default=None, description="New name")
    description: str | None = Field(default=None, description="New description")
    # Demographics
    gender: list[Gender] | None = Field(default=None, description="New gender list")
    age_group: list[AgeGroup] | None = Field(
        default=None, description="New age group list"
    )
    occupation: list[Occupation] | None = Field(
        default=None, description="New occupation list"
    )
    location: list[Location] | None = Field(
        default=None, description="New location list"
    )
    # Behavioral
    personality: list[Personality] | None = Field(
        default=None, description="New personality list"
    )
    communication_style: list[CommunicationStyle] | None = Field(
        default=None, description="New communication style list"
    )
    accent: list[Accent] | None = Field(
        default=None, description="New accent list. Voice only."
    )
    # Voice settings
    multilingual: bool | None = Field(
        default=None, description="Whether the persona supports multiple languages"
    )
    languages: list[Language] | None = Field(
        default=None, description="New languages list"
    )
    conversation_speed: list[ConversationSpeed] | None = Field(
        default=None, description="New conversation speed values. Voice only."
    )
    background_sound: bool | None = Field(
        default=None, description="Enable background sound. Voice only."
    )
    finished_speaking_sensitivity: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Finished speaking sensitivity (1-10). Voice only.",
    )
    interrupt_sensitivity: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Interrupt sensitivity (1-10). Voice only.",
    )
    keywords: list[str] | None = Field(default=None, description="New keywords list")
    # Chat settings
    tone: Tone | None = Field(default=None, description="New tone. Chat only.")
    verbosity: Verbosity | None = Field(
        default=None, description="New verbosity level. Chat only."
    )
    punctuation: Punctuation | None = Field(
        default=None, description="New punctuation style. Chat only."
    )
    emoji_usage: EmojiUsage | None = Field(
        default=None, description="New emoji usage level. Chat only."
    )
    slang_usage: SlangUsage | None = Field(
        default=None, description="New slang usage level. Chat only."
    )
    typos_frequency: TypoFrequency | None = Field(
        default=None, description="New typo frequency. Chat only."
    )
    regional_mix: RegionalMix | None = Field(
        default=None, description="New regional language mix level. Chat only."
    )
    metadata: dict[str, str] | None = Field(
        default=None, description="New custom key-value properties"
    )
    additional_instruction: str | None = Field(
        default=None, description="New additional instructions"
    )

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v):
        if v and isinstance(v, dict):
            for key, value in v.items():
                if not key or not str(key).strip():
                    raise ValueError("Custom property keys must be non-empty strings.")
                if not value or not str(value).strip():
                    raise ValueError(
                        f"Value for property '{key}' must be a non-empty string."
                    )
        return v


@register_tool
class UpdatePersonaTool(BaseTool):
    name = "update_persona"
    description = "Updates an existing persona. Only provided fields will be changed."
    category = "simulation"
    input_model = UpdatePersonaInput

    def execute(self, params: UpdatePersonaInput, context: ToolContext) -> ToolResult:

        persona, unresolved = _resolve_persona(params.persona_id, context)
        if unresolved:
            return unresolved

        if persona.persona_type == "system":
            return ToolResult.error(
                "System personas cannot be modified.",
                error_code="PERMISSION_DENIED",
            )

        updated_fields = []
        field_map = {
            "name": params.name,
            "description": params.description,
            "gender": params.gender,
            "age_group": params.age_group,
            "occupation": params.occupation,
            "location": params.location,
            "personality": params.personality,
            "communication_style": params.communication_style,
            "accent": params.accent,
            "multilingual": params.multilingual,
            "languages": params.languages,
            "conversation_speed": params.conversation_speed,
            "background_sound": params.background_sound,
            "finished_speaking_sensitivity": params.finished_speaking_sensitivity,
            "interrupt_sensitivity": params.interrupt_sensitivity,
            "keywords": params.keywords,
            "tone": params.tone,
            "verbosity": params.verbosity,
            "punctuation": params.punctuation,
            "emoji_usage": params.emoji_usage,
            "slang_usage": params.slang_usage,
            "typos_frequency": params.typos_frequency,
            "regional_mix": params.regional_mix,
            "metadata": params.metadata,
            "additional_instruction": params.additional_instruction,
        }

        for field_name, value in field_map.items():
            if value is not None:
                setattr(persona, field_name, value)
                updated_fields.append(field_name)

        if not updated_fields:
            return _candidate_personas_result(
                context,
                "Persona Update Requirements",
                "Provide at least one field to update, such as `name`, `description`, `tone`, or `verbosity`.",
            )

        persona.save(update_fields=updated_fields + ["updated_at"])

        info = key_value_block(
            [
                ("ID", f"`{persona.id}`"),
                ("Name", persona.name),
                ("Updated Fields", ", ".join(updated_fields)),
                ("Updated At", format_datetime(persona.updated_at)),
            ]
        )

        content = section("Persona Updated", info)

        return ToolResult(
            content=content,
            data={
                "id": str(persona.id),
                "name": persona.name,
                "updated_fields": updated_fields,
            },
        )

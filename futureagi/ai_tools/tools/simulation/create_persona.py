from typing import Any, Literal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    format_datetime,
    key_value_block,
    section,
)
from ai_tools.registry import register_tool

# Literal types mirroring Django model choices on simulate.models.persona.Persona.
# Exposing these as Literal produces JSON Schema ``enum`` arrays so MCP clients
# can present valid options to users without a round-trip.

SimulationType = Literal["voice", "text"]

Gender = Literal["male", "female"]

AgeGroup = Literal["18-25", "25-32", "32-40", "40-50", "50-60", "60+"]

Location = Literal["United States", "Canada", "United Kingdom", "Australia", "India"]

Occupation = Literal[
    "Student",
    "Teacher",
    "Engineer",
    "Doctor",
    "Nurse",
    "Business Owner",
    "Manager",
    "Sales Representative",
    "Customer Service",
    "Technician",
    "Consultant",
    "Accountant",
    "Marketing Professional",
    "Retired",
    "Homemaker",
    "Freelancer",
    "Other",
]

Personality = Literal[
    "Friendly and cooperative",
    "Professional and formal",
    "Cautious and skeptical",
    "Impatient and direct",
    "Detail-oriented",
    "Easy-going",
    "Anxious",
    "Confident",
    "Analytical",
    "Emotional",
    "Reserved",
    "Talkative",
]

CommunicationStyle = Literal[
    "Direct and concise",
    "Detailed and elaborate",
    "Casual and friendly",
    "Formal and polite",
    "Technical",
    "Simple and clear",
    "Questioning",
    "Assertive",
    "Passive",
    "Collaborative",
]

Accent = Literal["American", "Australian", "Indian", "Canadian", "Neutral"]

Language = Literal["English", "Hindi"]

ConversationSpeed = Literal["0.5", "0.75", "1.0", "1.25", "1.5"]

Tone = Literal["formal", "casual", "neutral"]

Verbosity = Literal["brief", "balanced", "detailed"]

Punctuation = Literal["clean", "minimal", "expressive", "erratic"]

EmojiUsage = Literal["never", "light", "regular", "heavy"]

SlangUsage = Literal["none", "light", "moderate", "heavy"]

TypoFrequency = Literal["none", "rare", "occasional", "frequent"]

RegionalMix = Literal["none", "light", "moderate", "heavy"]


def _get_persona_choices():
    """Lazy import to avoid circular imports at module load time."""
    from simulate.models.persona import Persona

    return {
        "simulation_type": [c[0] for c in Persona.SimulationTypeChoices.choices],
        "gender": [c[0] for c in Persona.GenderChoices.choices],
        "age_group": [c[0] for c in Persona.AgeGroupChoices.choices],
        "location": [c[0] for c in Persona.LocationChoices.choices],
        "occupation": [c[0] for c in Persona.ProfessionChoices.choices],
        "personality": [c[0] for c in Persona.PersonalityChoices.choices],
        "communication_style": [
            c[0] for c in Persona.CommunicationStyleChoices.choices
        ],
        "accent": [c[0] for c in Persona.AccentChoices.choices],
        "language": [c[0] for c in Persona.LanguageChoices.choices],
        "conversation_speed": [c[0] for c in Persona.ConversationSpeedChoices.choices],
        "tone": [c[0] for c in Persona.PersonaToneChoices.choices],
        "verbosity": [c[0] for c in Persona.PersonaVerbosityChoices.choices],
        "punctuation": [c[0] for c in Persona.PunctuationChoices.choices],
        "emoji_usage": [c[0] for c in Persona.EmojiUsageChoices.choices],
        "slang_usage": [c[0] for c in Persona.StandardUsageChoices.choices],
        "typos_frequency": [c[0] for c in Persona.TypoLevelChoices.choices],
        "regional_mix": [c[0] for c in Persona.StandardUsageChoices.choices],
    }


class CreatePersonaInput(PydanticBaseModel):
    name: str = Field(
        default="General Simulation Persona", description="Name of the persona"
    )
    description: str = Field(
        default="A general-purpose simulation persona for testing agent behavior.",
        description="Description of the persona",
    )
    simulation_type: SimulationType = Field(
        default="voice", description="Simulation type"
    )
    # Demographics
    gender: list[Gender] | None = Field(default=None, description="List of genders")
    age_group: list[AgeGroup] | None = Field(
        default=None, description="List of age groups"
    )
    occupation: list[Occupation] | None = Field(
        default=None, description="List of occupations"
    )
    location: list[Location] | None = Field(
        default=None, description="List of locations"
    )
    # Behavioral
    personality: list[Personality] | None = Field(
        default=None, description="List of personality types"
    )
    communication_style: list[CommunicationStyle] | None = Field(
        default=None, description="List of communication styles"
    )
    accent: list[Accent] | None = Field(
        default=None, description="List of accents. Voice only."
    )
    # Voice conversation settings
    multilingual: bool | None = Field(
        default=False, description="Whether the persona supports multiple languages"
    )
    languages: list[Language] | None = Field(
        default=None,
        description="List of languages. Required if multilingual=true.",
    )
    conversation_speed: list[ConversationSpeed] | None = Field(
        default=None,
        description="Conversation speed values. Voice only.",
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
    keywords: list[str] | None = Field(
        default=None, description="Free-form keywords for the persona"
    )
    # Chat settings
    tone: Tone | None = Field(default=None, description="Tone. Chat only.")
    verbosity: Verbosity | None = Field(
        default=None, description="Verbosity level. Chat only."
    )
    punctuation: Punctuation | None = Field(
        default=None, description="Punctuation style. Chat only."
    )
    emoji_usage: EmojiUsage | None = Field(
        default=None, description="Emoji usage level. Chat only."
    )
    slang_usage: SlangUsage | None = Field(
        default=None, description="Slang usage level. Chat only."
    )
    typos_frequency: TypoFrequency | None = Field(
        default=None, description="Typo frequency. Chat only."
    )
    regional_mix: RegionalMix | None = Field(
        default=None, description="Regional language mix level. Chat only."
    )
    metadata: dict[str, str] | None = Field(
        default=None, description="Custom key-value properties for the persona"
    )
    additional_instruction: str | None = Field(
        default=None, description="Additional behavior instructions for the persona"
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_aliases(cls, values: Any) -> Any:
        import json
        import re

        if not isinstance(values, dict):
            return values

        normalized = dict(values)

        def parse_jsonish(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            stripped = value.strip()
            if not stripped.startswith(("{", "[")):
                return value
            try:
                return json.loads(stripped)
            except (TypeError, ValueError):
                return value

        jsonish_fields = {
            "demographics",
            "personality",
            "metadata",
            "traits",
            "keywords",
            "gender",
            "age_group",
            "occupation",
            "location",
            "communication_style",
            "languages",
            "conversation_speed",
            "accent",
        }
        for field in jsonish_fields:
            if field in normalized:
                normalized[field] = parse_jsonish(normalized[field])

        def age_to_group(age: int) -> str:
            if age < 25:
                return "18-25"
            if age < 32:
                return "25-32"
            if age < 40:
                return "32-40"
            if age < 50:
                return "40-50"
            if age < 60:
                return "50-60"
            return "60+"

        def age_text_to_group(value: Any) -> str | None:
            if value is None:
                return None
            numbers = [int(match) for match in re.findall(r"\d+", str(value))]
            if not numbers:
                return None
            age = int(sum(numbers[:2]) / min(len(numbers), 2))
            return age_to_group(age)

        occupation_aliases = {
            "any": "Other",
            "professional": "Manager",
            "professional / decision-maker": "Manager",
            "decision-maker": "Manager",
            "decision maker": "Manager",
            "non-technical / first-time user": "Other",
            "non technical / first time user": "Other",
            "first-time user": "Other",
            "first time user": "Other",
            "technical": "Engineer",
            "product manager": "Manager",
            "manager": "Manager",
            "small business owner": "Business Owner",
            "business owner": "Business Owner",
            "support": "Customer Service",
            "customer support": "Customer Service",
            "student": "Student",
            "teacher": "Teacher",
            "retired teacher": "Retired",
            "retired": "Retired",
        }

        demographics = normalized.pop("demographics", None)
        if isinstance(demographics, dict):
            if demographics.get("gender") and not normalized.get("gender"):
                normalized["gender"] = demographics.get("gender")
            if not normalized.get("age_group"):
                age_group = (
                    demographics.get("age_group")
                    or demographics.get("age_range")
                    or age_text_to_group(demographics.get("age"))
                )
                mapped_age_group = age_text_to_group(age_group) or age_group
                if mapped_age_group:
                    normalized["age_group"] = [mapped_age_group]
            if demographics.get("occupation") and not normalized.get("occupation"):
                occupation = str(demographics.get("occupation")).strip()
                normalized["occupation"] = [
                    occupation_aliases.get(occupation.lower(), occupation)
                ]
            if demographics.get("location") and not normalized.get("location"):
                normalized["location"] = demographics.get("location")

            metadata = normalized.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            for key in ("tech_savviness", "age_range"):
                value = demographics.get(key)
                if value:
                    metadata[f"demographics_{key}"] = str(value)
            if metadata:
                normalized["metadata"] = metadata

        personality_payload = normalized.get("personality")
        if isinstance(personality_payload, dict):
            traits = personality_payload.get("traits")
            if traits:
                normalized["traits"] = traits
            normalized.pop("personality", None)

            metadata = normalized.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            instruction_parts = []
            for key in ("motivation", "emotional_state", "patience_level"):
                value = personality_payload.get(key)
                if value:
                    metadata[f"personality_{key}"] = str(value)
                    instruction_parts.append(f"{key.replace('_', ' ')}: {value}")
            if instruction_parts and not normalized.get("additional_instruction"):
                normalized["additional_instruction"] = "; ".join(instruction_parts)
            if metadata:
                normalized["metadata"] = metadata

        if "age" in normalized and not normalized.get("age_group"):
            try:
                age = int(str(normalized.get("age")).strip())
            except (TypeError, ValueError):
                age = None
            if age is not None:
                normalized["age_group"] = [age_to_group(age)]

        if "traits" in normalized and not normalized.get("personality"):
            traits = normalized.get("traits")
            if isinstance(traits, str):
                traits = [traits]
            trait_text = " ".join(str(t).lower() for t in (traits or []))
            personality = []
            if any(word in trait_text for word in ("confident", "decisive", "goal")):
                personality.append("Confident")
            if any(word in trait_text for word in ("detail", "efficient")):
                personality.append("Detail-oriented")
            if any(word in trait_text for word in ("angry", "aggressive", "impatient")):
                personality.append("Impatient and direct")
            if any(
                word in trait_text for word in ("confused", "uncertain", "overwhelmed")
            ):
                personality.append("Anxious")
            if any(
                word in trait_text
                for word in ("cautious", "skeptical", "evasive", "deceptive")
            ):
                personality.append("Cautious and skeptical")
            if personality:
                normalized["personality"] = list(dict.fromkeys(personality))

        communication_aliases = {
            "direct": "Direct and concise",
            "aggressive": "Assertive",
            "confrontational": "Assertive",
            "assertive": "Assertive",
            "hesitant": "Questioning",
            "indirect": "Questioning",
            "evasive": "Questioning",
            "casual": "Casual and friendly",
            "formal": "Formal and polite",
            "technical": "Technical",
            "simple": "Simple and clear",
            "verbose": "Detailed and elaborate",
            "detailed": "Detailed and elaborate",
        }
        text_style = normalized.get("text_style")
        if text_style and not normalized.get("communication_style"):
            normalized["communication_style"] = text_style
        if str(text_style or "").strip().lower() in {"verbose", "detailed"} and not normalized.get("verbosity"):
            normalized["verbosity"] = "detailed"

        style = normalized.get("communication_style")
        if isinstance(style, str):
            normalized["communication_style"] = [
                communication_aliases.get(style.strip().lower(), style)
            ]

        age_group_aliases = {
            "child": "18-25",
            "teen": "18-25",
            "young": "18-25",
            "young_adult": "18-25",
            "young adult": "18-25",
            "adult": "32-40",
            "middle_aged": "40-50",
            "middle aged": "40-50",
            "older_adult": "50-60",
            "older adult": "50-60",
            "senior": "60+",
            "elderly": "60+",
        }
        personality_aliases = {
            "determined": "Confident",
            "decisive": "Confident",
            "goal-oriented": "Confident",
            "goal oriented": "Confident",
            "aggressive": "Impatient and direct",
            "angry": "Impatient and direct",
            "frustrated": "Impatient and direct",
            "impatient": "Impatient and direct",
            "hesitant": "Anxious",
            "confused": "Anxious",
            "uncertain": "Anxious",
            "deceptive": "Cautious and skeptical",
            "evasive": "Cautious and skeptical",
            "fraud-risk": "Cautious and skeptical",
            "fraud risk": "Cautious and skeptical",
            "skeptical": "Cautious and skeptical",
            "friendly": "Friendly and cooperative",
            "professional": "Professional and formal",
            "formal": "Professional and formal",
            "detail-oriented": "Detail-oriented",
            "detail oriented": "Detail-oriented",
            "analytical": "Analytical",
            "emotional": "Emotional",
            "reserved": "Reserved",
            "talkative": "Talkative",
        }
        verbosity_aliases = {
            "low": "brief",
            "short": "brief",
            "concise": "brief",
            "medium": "balanced",
            "moderate": "balanced",
            "normal": "balanced",
            "high": "detailed",
            "verbose": "detailed",
            "long": "detailed",
        }
        gender_aliases = {
            "m": "male",
            "man": "male",
            "male": "male",
            "f": "female",
            "woman": "female",
            "female": "female",
        }

        def map_location(value: Any) -> str | None:
            text = str(value or "").strip()
            if not text:
                return None
            lower = text.lower()
            if lower in {"united states", "usa", "us", "u.s.", "america"}:
                return "United States"
            if lower in {"canada"}:
                return "Canada"
            if lower in {"united kingdom", "uk", "u.k.", "england"}:
                return "United Kingdom"
            if lower in {"australia"}:
                return "Australia"
            if lower in {"india"}:
                return "India"
            if any(
                token in lower
                for token in (
                    "san francisco",
                    "chicago",
                    "austin",
                    "new york",
                    "california",
                    ", ca",
                    ", il",
                    ", tx",
                    ", ny",
                    "usa",
                    "united states",
                )
            ):
                return "United States"
            if any(token in lower for token in ("toronto", "vancouver", "canada")):
                return "Canada"
            if any(token in lower for token in ("london", "manchester", "uk")):
                return "United Kingdom"
            if any(token in lower for token in ("sydney", "melbourne", "australia")):
                return "Australia"
            if any(token in lower for token in ("india", "delhi", "mumbai", "bangalore", "bengaluru")):
                return "India"
            return None

        def normalize_alias_list(field: str, aliases: dict[str, str]) -> None:
            value = normalized.get(field)
            if value is None:
                return
            values = value if isinstance(value, list) else [value]
            normalized[field] = [
                aliases.get(str(item).strip().lower(), item)
                for item in values
                if str(item).strip()
            ]

        normalize_alias_list("age_group", age_group_aliases)
        normalize_alias_list("personality", personality_aliases)
        normalize_alias_list("occupation", occupation_aliases)
        normalize_alias_list("gender", gender_aliases)
        normalize_alias_list("communication_style", communication_aliases)

        raw_locations = normalized.get("location")
        if raw_locations is not None:
            location_values = raw_locations if isinstance(raw_locations, list) else [raw_locations]
            mapped_locations = []
            raw_unmapped = []
            for location in location_values:
                mapped = map_location(location)
                if mapped:
                    mapped_locations.append(mapped)
                else:
                    raw_unmapped.append(str(location))
            normalized["location"] = list(dict.fromkeys(mapped_locations)) or None
            if raw_unmapped:
                metadata = normalized.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["raw_location"] = ", ".join(raw_unmapped)
                normalized["metadata"] = metadata

        verbosity = normalized.get("verbosity")
        if isinstance(verbosity, str):
            normalized["verbosity"] = verbosity_aliases.get(
                verbosity.strip().lower(), verbosity
            )

        list_fields = {
            "gender",
            "age_group",
            "occupation",
            "location",
            "personality",
            "communication_style",
            "accent",
            "languages",
            "conversation_speed",
            "keywords",
        }
        for field in list_fields:
            value = normalized.get(field)
            if value is None or isinstance(value, list):
                continue
            if field == "gender" and str(value).strip().lower() in {
                "neutral",
                "nonbinary",
                "unknown",
                "any",
            }:
                normalized[field] = None
            else:
                normalized[field] = [value]

        return normalized

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
class CreatePersonaTool(BaseTool):
    name = "create_persona"
    description = (
        "Creates a new workspace-level persona for simulations. "
        "Configure demographics, behavioral traits, and speech/text settings."
    )
    category = "simulation"
    input_model = CreatePersonaInput

    def execute(self, params: CreatePersonaInput, context: ToolContext) -> ToolResult:

        from simulate.models.persona import Persona

        # Cross-field validation: multilingual requires languages
        if params.multilingual and not params.languages:
            return ToolResult.error(
                "At least one language is required when multilingual is enabled.",
                error_code="VALIDATION_ERROR",
            )

        # Check for system persona with same name
        if Persona.no_workspace_objects.filter(
            name__iexact=params.name,
            persona_type=Persona.PersonaType.SYSTEM,
        ).exists():
            return ToolResult.error(
                "A system persona with this name already exists. Please choose a different name.",
                error_code="VALIDATION_ERROR",
            )

        # Check for workspace persona with same name
        existing_persona = Persona.no_workspace_objects.filter(
            name__iexact=params.name,
            workspace=context.workspace,
            organization=context.organization,
            persona_type=Persona.PersonaType.WORKSPACE,
        ).first()
        if existing_persona:
            info = key_value_block(
                [
                    ("ID", f"`{existing_persona.id}`"),
                    ("Name", existing_persona.name),
                    ("Type", "workspace"),
                    ("Simulation Type", existing_persona.simulation_type),
                    ("Created", format_datetime(existing_persona.created_at)),
                ]
            )
            return ToolResult(
                content=section("Persona Already Exists", info),
                data={
                    "id": str(existing_persona.id),
                    "name": existing_persona.name,
                    "simulation_type": existing_persona.simulation_type,
                    "persona_type": "workspace",
                    "already_exists": True,
                },
            )

        persona = Persona(
            name=params.name,
            description=params.description,
            persona_type="workspace",
            simulation_type=params.simulation_type,
            gender=params.gender or [],
            age_group=params.age_group or [],
            occupation=params.occupation or [],
            location=params.location or [],
            personality=params.personality or [],
            communication_style=params.communication_style or [],
            accent=params.accent or [],
            multilingual=params.multilingual or False,
            languages=params.languages or [],
            conversation_speed=params.conversation_speed or [],
            background_sound=params.background_sound,
            finished_speaking_sensitivity=params.finished_speaking_sensitivity,
            interrupt_sensitivity=params.interrupt_sensitivity,
            keywords=params.keywords or [],
            tone=params.tone,
            verbosity=params.verbosity,
            punctuation=params.punctuation,
            emoji_usage=params.emoji_usage,
            slang_usage=params.slang_usage,
            typos_frequency=params.typos_frequency,
            regional_mix=params.regional_mix,
            metadata=params.metadata or {},
            additional_instruction=params.additional_instruction,
            organization=context.organization,
            workspace=context.workspace,
        )
        persona.save()

        def list_str(val):
            if val and isinstance(val, list):
                return ", ".join(str(v) for v in val)
            return "—"

        info = key_value_block(
            [
                ("ID", f"`{persona.id}`"),
                ("Name", persona.name),
                ("Type", "workspace"),
                ("Simulation Type", persona.simulation_type),
                ("Gender", list_str(persona.gender)),
                ("Personality", list_str(persona.personality)),
                ("Tone", persona.tone or "—"),
                ("Verbosity", persona.verbosity or "—"),
                ("Created", format_datetime(persona.created_at)),
            ]
        )

        content = section("Persona Created", info)

        return ToolResult(
            content=content,
            data={
                "id": str(persona.id),
                "name": persona.name,
                "simulation_type": persona.simulation_type,
                "persona_type": "workspace",
            },
        )

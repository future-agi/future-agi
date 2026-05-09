import uuid

from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models.agent_definition import AgentTypeChoices
from simulate.utils.persona_utils import persona_first as _first
from tfc.utils.base_model import BaseModel


class Persona(BaseModel):
    """
    Model to store persona configurations for simulations.
    Supports both system-level (platform-wide) and workspace-level (user-created) personas.
    """

    class PersonaType(models.TextChoices):
        SYSTEM = "system", "System"
        WORKSPACE = "workspace", "Workspace"

    # Reference choices (not enforced, just for guidance)
    class GenderChoices(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    class AgeGroupChoices(models.TextChoices):
        AGE_18_25 = "18-25", "18-25"
        AGE_25_32 = "25-32", "25-32"
        AGE_32_40 = "32-40", "32-40"
        AGE_40_50 = "40-50", "40-50"
        AGE_50_60 = "50-60", "50-60"
        AGE_60_PLUS = "60+", "60+"

    class LocationChoices(models.TextChoices):
        UNITED_STATES = "United States", "United States"
        CANADA = "Canada", "Canada"
        UNITED_KINGDOM = "United Kingdom", "United Kingdom"
        AUSTRALIA = "Australia", "Australia"
        INDIA = "India", "India"

    class ProfessionChoices(models.TextChoices):
        STUDENT = "Student", "Student"
        TEACHER = "Teacher", "Teacher"
        ENGINEER = "Engineer", "Engineer"
        DOCTOR = "Doctor", "Doctor"
        NURSE = "Nurse", "Nurse"
        BUSINESS_OWNER = "Business Owner", "Business Owner"
        MANAGER = "Manager", "Manager"
        SALES_REPRESENTATIVE = "Sales Representative", "Sales Representative"
        CUSTOMER_SERVICE = "Customer Service", "Customer Service"
        TECHNICIAN = "Technician", "Technician"
        CONSULTANT = "Consultant", "Consultant"
        ACCOUNTANT = "Accountant", "Accountant"
        MARKETING_PROFESSIONAL = "Marketing Professional", "Marketing Professional"
        RETIRED = "Retired", "Retired"
        HOMEMAKER = "Homemaker", "Homemaker"
        FREELANCER = "Freelancer", "Freelancer"
        OTHER = "Other", "Other"

    class PersonalityChoices(models.TextChoices):
        FRIENDLY_COOPERATIVE = "Friendly and cooperative", "Friendly and cooperative"
        PROFESSIONAL_FORMAL = "Professional and formal", "Professional and formal"
        CAUTIOUS_SKEPTICAL = "Cautious and skeptical", "Cautious and skeptical"
        IMPATIENT_DIRECT = "Impatient and direct", "Impatient and direct"
        DETAIL_ORIENTED = "Detail-oriented", "Detail-oriented"
        EASY_GOING = "Easy-going", "Easy-going"
        ANXIOUS = "Anxious", "Anxious"
        CONFIDENT = "Confident", "Confident"
        ANALYTICAL = "Analytical", "Analytical"
        EMOTIONAL = "Emotional", "Emotional"
        RESERVED = "Reserved", "Reserved"
        TALKATIVE = "Talkative", "Talkative"

    class CommunicationStyleChoices(models.TextChoices):
        DIRECT_CONCISE = "Direct and concise", "Direct and concise"
        DETAILED_ELABORATE = "Detailed and elaborate", "Detailed and elaborate"
        CASUAL_FRIENDLY = "Casual and friendly", "Casual and friendly"
        FORMAL_POLITE = "Formal and polite", "Formal and polite"
        TECHNICAL = "Technical", "Technical"
        SIMPLE_CLEAR = "Simple and clear", "Simple and clear"
        QUESTIONING = "Questioning", "Questioning"
        ASSERTIVE = "Assertive", "Assertive"
        PASSIVE = "Passive", "Passive"
        COLLABORATIVE = "Collaborative", "Collaborative"

    class AccentChoices(models.TextChoices):
        AMERICAN = "American", "American"
        AUSTRALIAN = "Australian", "Australian"
        INDIAN = "Indian", "Indian"
        CANADIAN = "Canadian", "Canadian"
        NEUTRAL = "Neutral", "Neutral"

    class LanguageChoices(models.TextChoices):
        ENGLISH = "English", "English"
        HINDI = "Hindi", "Hindi"

    class ConversationSpeedChoices(models.TextChoices):
        VERY_SLOW = "0.5", "Very slow"
        SLOW = "0.75", "Slow"
        MODERATE = "1.0", "Moderate"
        FAST = "1.25", "Fast"
        VERY_FAST = "1.5", "Very fast"

    SimulationTypeChoices = AgentTypeChoices

    class PunctuationChoices(models.TextChoices):
        CLEAN = "clean", "Clean"
        MINIMAL = "minimal", "Minimal"
        EXPRESSIVE = "expressive", "Expressive"
        ERRATIC = "erratic", "Erratic"

    class EmojiUsageChoices(models.TextChoices):
        NEVER = "never", "Never"
        LIGHT = "light", "Light"
        REGULAR = "regular", "Regular"
        HEAVY = "heavy", "Heavy"

    class StandardUsageChoices(models.TextChoices):
        NONE = "none", "None"
        MODERATE = "moderate", "Moderate"
        HEAVY = "heavy", "Heavy"
        LIGHT = "light", "Light"

    class PersonaToneChoices(models.TextChoices):
        FORMAL = "formal", "Formal"
        CASUAL = "casual", "Casual"
        NEUTRAL = "neutral", "Neutral"

    class PersonaVerbosityChoices(models.TextChoices):
        BRIEF = "brief", "Brief"
        BALANCED = "balanced", "Balanced"
        DETAILED = "detailed", "Detailed"

    class TypoLevelChoices(models.TextChoices):
        NONE = "none", "None"
        RARE = "rare", "Rare"
        OCCASIONAL = "occasional", "Occasional"
        FREQUENT = "frequent", "Frequent"

    # Primary fields
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    persona_type = models.CharField(
        max_length=20,
        choices=PersonaType.choices,
        default=PersonaType.WORKSPACE,
        help_text="Type of persona (system or workspace-level)",
    )

    persona_id = models.IntegerField(
        default=0, help_text="Unique identifier for system personas (e.g., 1, 2, 3)"
    )

    name = models.CharField(max_length=255, help_text="Name of the persona")

    description = models.TextField(
        blank=True, null=True, help_text="Description of the persona"
    )

    # Organization and Workspace
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="personas",
        null=True,
        blank=True,
        help_text="Organization this persona belongs to (null for system personas)",
    )

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="personas",
        null=True,
        blank=True,
        help_text="Workspace this persona belongs to (null for system personas)",
    )

    # Basic Information (choices are reference only, not enforced)
    # All fields support multiple values as lists
    gender = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of genders for the persona (e.g., ['male'], ['female'])",
    )

    age_group = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of age groups for the persona (e.g., ['18-25'], ['25-32'])",
    )

    occupation = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of occupations/professions for the persona (e.g., ['Engineer'], ['Teacher'])",
    )

    location = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of locations for the persona (e.g., ['United States'], ['Canada'])",
    )

    # Behavioral Profile
    personality = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of personality types for the persona (e.g., ['Friendly and cooperative'])",
    )

    communication_style = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of communication styles for the persona (e.g., ['Direct and concise'])",
    )

    # Speech Profile
    multilingual = models.BooleanField(
        default=False,
        null=True,
        help_text="Whether the persona supports multiple languages",
    )

    languages = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of languages the persona speaks (e.g., ['English', 'Hindi'])",
    )

    accent = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of accents for the persona (e.g., ['American'], ['Australian'])",
    )

    conversation_speed = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of conversation speeds (e.g., ['1.0'], ['1.25'])",
    )

    # Advanced Settings
    background_sound = models.BooleanField(
        default=False,
        null=True,
        blank=True,
        help_text="Whether background sound is enabled (null=not specified, True/False for enabled/disabled)",
    )

    finished_speaking_sensitivity = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of sensitivities for detecting when persona finished speaking (e.g., ['5'], ['6'])",
    )

    interrupt_sensitivity = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of sensitivities for allowing interruptions (e.g., ['5'], ['6'])",
    )

    # Keywords/Tags for the persona
    keywords = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="List of keywords/tags describing the persona (e.g., ['Knowledgeable', 'Patient', 'Helpful'])",
    )

    # Additional metadata
    metadata = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text="Additional metadata for the persona (speech clarity, base emotion, etc.)",
    )

    # Additional instructions for persona behavior
    additional_instruction = models.TextField(
        blank=True,
        null=True,
        help_text="Additional instructions for how this persona should behave",
    )

    # System-level persona flag
    is_default = models.BooleanField(
        default=False,
        null=True,
        help_text="Whether this is a default/recommended persona",
    )

    simulation_type = models.CharField(
        max_length=20,
        choices=SimulationTypeChoices.choices,
        default=SimulationTypeChoices.VOICE,
        help_text="Type of simulation for the persona",
    )

    punctuation = models.CharField(
        max_length=20,
        choices=PunctuationChoices.choices,
        help_text="Punctuation style for the persona",
        null=True,
        blank=True,
    )

    slang_usage = models.CharField(
        max_length=20,
        choices=StandardUsageChoices.choices,
        help_text="Slang usage for the persona",
        null=True,
        blank=True,
    )

    typos_frequency = models.CharField(
        max_length=20,
        choices=TypoLevelChoices.choices,
        help_text="Typos frequency for the persona",
        null=True,
        blank=True,
    )

    regional_mix = models.CharField(
        max_length=20,
        choices=StandardUsageChoices.choices,
        help_text="Regional mix for the persona",
        null=True,
        blank=True,
    )

    emoji_usage = models.CharField(
        max_length=20,
        choices=EmojiUsageChoices.choices,
        help_text="Emoji usage for the persona",
        null=True,
        blank=True,
    )

    tone = models.CharField(
        max_length=20,
        choices=PersonaToneChoices.choices,
        help_text="Tone for the persona",
        null=True,
        blank=True,
    )

    verbosity = models.CharField(
        max_length=20,
        choices=PersonaVerbosityChoices.choices,
        help_text="Verbosity for the persona",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "simulate_personas"
        verbose_name = "Persona"
        verbose_name_plural = "Personas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["persona_type"]),
            models.Index(fields=["organization", "workspace"]),
        ]
        constraints = [
            # System personas should not have organization or workspace
            models.CheckConstraint(
                check=(
                    models.Q(
                        persona_type="system",
                        organization__isnull=True,
                        workspace__isnull=True,
                    )
                    | models.Q(persona_type="workspace")
                ),
                name="system_persona_no_org_workspace",
            ),
            # Workspace personas must have organization
            models.CheckConstraint(
                check=(
                    models.Q(persona_type="workspace", organization__isnull=False)
                    | models.Q(persona_type="system")
                ),
                name="workspace_persona_has_org",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_persona_type_display()})"

    def clean(self):
        """Custom validation - minimal validation for now"""
        if not self.name or not self.name.strip():
            raise ValidationError({"name": "Name cannot be empty or just whitespace."})

        # Validate system personas don't have organization/workspace
        if self.persona_type == self.PersonaType.SYSTEM:
            if self.organization or self.workspace:
                raise ValidationError(
                    {
                        "persona_type": "System personas cannot be associated with an organization or workspace."
                    }
                )

        # Validate workspace personas have organization
        if self.persona_type == self.PersonaType.WORKSPACE:
            if not self.organization:
                raise ValidationError(
                    {
                        "organization": "Workspace personas must be associated with an organization."
                    }
                )

    def to_voice_mapper_dict(self):
        """
        Convert persona to dictionary format expected by voice_mapper.py.
        The voice mapper consumes a single string per attribute; only the first
        selected value is used. _first() warns when more than one value was
        selected so the silent drop is visible in logs.
        """
        return {
            "gender": _first(self.gender, "gender", "male"),
            "age_group": _first(self.age_group, "age_group", "18-25"),
            "profession": _first(self.occupation, "occupation", "Other"),
            "location": _first(self.location, "location", "United States"),
            "personality": _first(
                self.personality, "personality", "Friendly and cooperative"
            ),
            "communication_style": _first(
                self.communication_style, "communication_style", "Direct and concise"
            ),
            "accent": _first(self.accent, "accent", "Neutral"),
            "language": _first(self.languages, "languages", "English"),
            "conversation_speed": _first(
                self.conversation_speed, "conversation_speed", "1.0"
            ),
            "background_sound": (
                str(self.background_sound).lower()
                if self.background_sound is not None
                else "false"
            ),
            "finished_speaking_sensitivity": _first(
                self.finished_speaking_sensitivity,
                "finished_speaking_sensitivity",
                "5",
            ),
            "interrupt_sensitivity": _first(
                self.interrupt_sensitivity, "interrupt_sensitivity", "5"
            ),
        }

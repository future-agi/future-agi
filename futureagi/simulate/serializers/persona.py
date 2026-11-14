from rest_framework import serializers

from simulate.models import Persona
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_workspace,
)


class PersonaSerializer(serializers.ModelSerializer):
    """
    Serializer for Persona model - returns business fields only.
    Supports both API contract field names (profession, language, custom_properties)
    and model field names (occupation, languages, metadata) for updates.
    """

    persona_type_display = serializers.CharField(
        source="get_persona_type_display", read_only=True
    )

    # API contract fields (write-only, for updates)
    profession = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        write_only=True,
        source="occupation",
    )
    language = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        write_only=True,
        source="languages",
    )
    custom_properties = serializers.JSONField(
        required=False,
        allow_null=True,
        write_only=True,
        source="metadata",
    )

    class Meta:
        model = Persona
        fields = [
            "id",
            "persona_type",
            "persona_type_display",
            "name",
            "description",
            "gender",
            "age_group",
            "occupation",
            "location",
            "personality",
            "communication_style",
            "multilingual",
            "languages",
            "accent",
            "conversation_speed",
            "background_sound",
            "finished_speaking_sensitivity",
            "interrupt_sensitivity",
            "keywords",
            "metadata",
            "additional_instruction",
            "is_default",
            "created_at",
            "updated_at",
            # API contract fields (write-only)
            "profession",
            "language",
            "custom_properties",
            "simulation_type",
            "punctuation",
            "slang_usage",
            "typos_frequency",
            "regional_mix",
            "emoji_usage",
            "tone",
            "verbosity",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "persona_type",
            "persona_type_display",
            "is_default",
            "simulation_type",
        ]

    def validate(self, attrs):
        """Custom validation"""
        # Note: organization and workspace are not in the serializer fields
        # They are handled automatically in the view layer from user context
        # No validation needed here since they're set internally
        return attrs

    def get_simulation_type(self, obj):
        """Return simulation_type or default to 'voice' if null or not present"""
        if obj.simulation_type:
            return obj.simulation_type
        return Persona.SimulationTypeChoices.VOICE


class PersonaListSerializer(serializers.ModelSerializer):
    """Serializer for listing personas - returns business fields only"""

    persona_type_display = serializers.CharField(
        source="get_persona_type_display", read_only=True
    )
    simulation_type = serializers.SerializerMethodField()

    class Meta:
        model = Persona
        fields = [
            "id",
            "persona_type",
            "persona_type_display",
            "name",
            "description",
            "gender",
            "age_group",
            "occupation",
            "location",
            "personality",
            "communication_style",
            "multilingual",
            "languages",
            "accent",
            "conversation_speed",
            "background_sound",
            "finished_speaking_sensitivity",
            "interrupt_sensitivity",
            "keywords",
            "metadata",
            "additional_instruction",
            "is_default",
            "created_at",
            "updated_at",
            "simulation_type",
            "punctuation",
            "slang_usage",
            "typos_frequency",
            "regional_mix",
            "emoji_usage",
            "tone",
            "verbosity",
        ]
        read_only_fields = [
            "id",
            "persona_type",
            "persona_type_display",
            "name",
            "description",
            "gender",
            "age_group",
            "occupation",
            "location",
            "personality",
            "communication_style",
            "multilingual",
            "languages",
            "accent",
            "conversation_speed",
            "background_sound",
            "finished_speaking_sensitivity",
            "interrupt_sensitivity",
            "keywords",
            "metadata",
            "additional_instruction",
            "is_default",
            "created_at",
            "updated_at",
            "simulation_type",
            "punctuation",
            "slang_usage",
            "typos_frequency",
            "regional_mix",
            "emoji_usage",
            "tone",
            "verbosity",
        ]

    def get_simulation_type(self, obj):
        """Return simulation_type or default to 'voice' if null or not present"""
        if obj.simulation_type:
            return obj.simulation_type
        return "voice"


class PersonaCreateSerializer(serializers.Serializer):
    """
    Serializer for creating workspace-level personas.
    Follows API contract with field mappings to model:
    - profession -> occupation
    - language -> languages
    - custom_properties -> metadata
    - additional_instruction -> additional_instruction

    All persona attribute fields now accept lists to support multiple values.
    """

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=True, allow_blank=False, min_length=1)
    gender = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    age_group = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    location = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    profession = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
        source="occupation",
    )
    personality = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    communication_style = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    accent = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    multilingual = serializers.BooleanField(required=False, default=False)
    language = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
        source="languages",
    )
    conversation_speed = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    background_sound = serializers.BooleanField(required=False, allow_null=True)
    finished_speaking_sensitivity = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    interrupt_sensitivity = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    keywords = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
    )
    custom_properties = serializers.JSONField(
        required=False, allow_null=True, default=dict, source="metadata"
    )
    additional_instruction = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=""
    )

    simulation_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="voice"
    )

    tone = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="casual"
    )
    punctuation = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="clean"
    )
    slang_usage = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="light"
    )
    typos_frequency = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="rare"
    )
    regional_mix = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="light"
    )
    emoji_usage = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="light"
    )

    verbosity = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default="balanced"
    )

    def validate_simulation_type(self, value):
        """Validate simulation type choices"""
        if value is not None and value not in [
            choice[0] for choice in Persona.SimulationTypeChoices.choices
        ]:
            raise serializers.ValidationError(
                f"Invalid simulation type: {value}. Valid choices: {Persona.SimulationTypeChoices.choices}"
            )
        return value

    def validate_gender(self, value):
        """Validate gender choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.GenderChoices.choices]
            invalid = [v for v in value if v not in valid_choices]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid gender values: {invalid}. Valid choices: {valid_choices}"
                )
        return value

    def validate_age_group(self, value):
        """Validate age_group choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.AgeGroupChoices.choices]
            invalid = [v for v in value if v not in valid_choices]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid age_group values: {invalid}. Valid choices: {valid_choices}"
                )
        return value

    def validate_location(self, value):
        """Validate location choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.LocationChoices.choices]
            invalid = [v for v in value if v not in valid_choices]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid location values: {invalid}. Valid choices: {valid_choices}"
                )
        return value

    # def validate_profession(self, value):
    #     """Validate profession/occupation choices (field name 'profession' maps to model 'occupation')"""
    #     if value:
    #         valid_choices = [choice[0] for choice in Persona.ProfessionChoices.choices]
    #         invalid = [v for v in value if v not in valid_choices]
    #         if invalid:
    #             raise serializers.ValidationError(
    #                 f"Invalid profession values: {invalid}. Valid choices: {valid_choices}"
    #             )
    #     return value

    def validate_personality(self, value):
        """Validate personality choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.PersonalityChoices.choices]
            invalid = [v for v in value if v not in valid_choices]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid personality values: {invalid}. Valid choices: {valid_choices}"
                )
        return value

    def validate_communication_style(self, value):
        """Validate communication_style choices"""
        if value:
            valid_choices = [
                choice[0] for choice in Persona.CommunicationStyleChoices.choices
            ]
            invalid = [v for v in value if v not in valid_choices]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid communication_style values: {invalid}. Valid choices: {valid_choices}"
                )
        return value

    # def validate_accent(self, value):
    #     """Validate accent choices"""
    #     if value:
    #         valid_choices = [choice[0] for choice in Persona.AccentChoices.choices]
    #         invalid = [v for v in value if v not in valid_choices]
    #         if invalid:
    #             raise serializers.ValidationError(
    #                 f"Invalid accent values: {invalid}. Valid choices: {valid_choices}"
    #             )
    #     return value

    # def validate_language(self, value):
    #     """Validate language choices (field name 'language' maps to model 'languages')"""
    #     if value:
    #         valid_choices = [choice[0] for choice in Persona.LanguageChoices.choices]
    #         invalid = [v for v in value if v not in valid_choices]
    #         if invalid:
    #             raise serializers.ValidationError(
    #                 f"Invalid language values: {invalid}. Valid choices: {valid_choices}"
    #             )
    #     return value

    def validate_conversation_speed(self, value):
        """Validate conversation_speed choices"""
        if value:
            valid_choices = [
                choice[0] for choice in Persona.ConversationSpeedChoices.choices
            ]
            invalid = [v for v in value if v not in valid_choices]
            if invalid:
                raise serializers.ValidationError(
                    f"Invalid conversation_speed values: {invalid}. Valid choices: {valid_choices}"
                )
        return value

    def validate_finished_speaking_sensitivity(self, value):
        """Validate finished_speaking_sensitivity values are integers in range 1-10"""
        if value:
            for v in value:
                try:
                    num = int(v)
                except (ValueError, TypeError):
                    raise serializers.ValidationError(
                        f"Invalid finished_speaking_sensitivity value: '{v}'. Must be an integer between 1 and 10."
                    ) from None
                if num < 1 or num > 10:
                    raise serializers.ValidationError(
                        f"Invalid finished_speaking_sensitivity value: {num}. Must be between 1 and 10."
                    )
        return value

    def validate_interrupt_sensitivity(self, value):
        """Validate interrupt_sensitivity values are integers in range 1-10"""
        if value:
            for v in value:
                try:
                    num = int(v)
                except (ValueError, TypeError):
                    raise serializers.ValidationError(
                        f"Invalid interrupt_sensitivity value: '{v}'. Must be an integer between 1 and 10."
                    ) from None
                if num < 1 or num > 10:
                    raise serializers.ValidationError(
                        f"Invalid interrupt_sensitivity value: {num}. Must be between 1 and 10."
                    )
        return value

    def validate_tone(self, value):
        """Validate tone choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.PersonaToneChoices.choices]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid tone value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate_verbosity(self, value):
        """Validate verbosity choices"""
        if value:
            valid_choices = [
                choice[0] for choice in Persona.PersonaVerbosityChoices.choices
            ]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid verbosity value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate_punctuation(self, value):
        """Validate punctuation choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.PunctuationChoices.choices]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid punctuation value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate_emoji_usage(self, value):
        """Validate emoji_usage choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.EmojiUsageChoices.choices]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid emoji_usage value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate_slang_usage(self, value):
        """Validate slang_usage choices"""
        if value:
            valid_choices = [
                choice[0] for choice in Persona.StandardUsageChoices.choices
            ]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid slang_usage value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate_typos_frequency(self, value):
        """Validate typos_frequency choices"""
        if value:
            valid_choices = [choice[0] for choice in Persona.TypoLevelChoices.choices]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid typos_frequency value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate_regional_mix(self, value):
        """Validate regional_mix choices"""
        if value:
            valid_choices = [
                choice[0] for choice in Persona.StandardUsageChoices.choices
            ]
            if value not in valid_choices:
                raise serializers.ValidationError(
                    f"Invalid regional_mix value: {value}. Valid choices: {valid_choices}"
                )
        return value

    def validate(self, attrs):
        """Cross-field validation"""
        # If multilingual is True, languages must be non-empty
        multilingual = attrs.get("multilingual", False)
        languages = attrs.get("languages", [])
        if multilingual and not languages:
            raise serializers.ValidationError(
                {
                    "language": "At least one language is required when multilingual is enabled."
                }
            )

        # Validate custom_properties keys and values are non-empty strings
        metadata = attrs.get("metadata")
        if metadata and isinstance(metadata, dict):
            for key, value in metadata.items():
                if not key or not str(key).strip():
                    raise serializers.ValidationError(
                        {
                            "custom_properties": "Property keys must be non-empty strings."
                        }
                    )
                if not value or not str(value).strip():
                    raise serializers.ValidationError(
                        {
                            "custom_properties": f"Value for property '{key}' must be a non-empty string."
                        }
                    )

        return attrs

    def validate_background_sound(self, value):
        """Convert string boolean to actual boolean, preserve None"""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ["true", "1", "yes"]
        return bool(value)

    def create(self, validated_data):
        """Create workspace-level persona"""
        # Get organization and workspace from user context
        request = self.context.get("request")
        user = request.user if request else None

        organization = get_current_organization()
        if not organization and user:
            organization = getattr(user, "organization", None)

        workspace = get_current_workspace()

        # Create persona as workspace-level
        persona = Persona.objects.create(
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=organization,
            workspace=workspace,
            **validated_data,
        )

        return persona


class PersonaDuplicateRequestSerializer(serializers.Serializer):
    """Request payload for duplicating a persona under a new workspace name."""

    name = serializers.CharField(max_length=255)


class PersonaDuplicateResponseSerializer(serializers.Serializer):
    """GeneralMethods success envelope returned by persona duplicate endpoints."""

    status = serializers.BooleanField(default=True)
    result = PersonaSerializer(read_only=True)


class PersonaFieldOptionsSerializer(serializers.Serializer):
    """Serializer to return field options/choices for persona creation"""

    gender_choices = serializers.SerializerMethodField()
    age_group_choices = serializers.SerializerMethodField()
    location_choices = serializers.SerializerMethodField()
    profession_choices = serializers.SerializerMethodField()
    personality_choices = serializers.SerializerMethodField()
    communication_style_choices = serializers.SerializerMethodField()
    accent_choices = serializers.SerializerMethodField()
    language_choices = serializers.SerializerMethodField()
    conversation_speed_choices = serializers.SerializerMethodField()
    tone_choices = serializers.SerializerMethodField()
    verbosity_choices = serializers.SerializerMethodField()
    punctuation_choices = serializers.SerializerMethodField()
    emoji_usage_choices = serializers.SerializerMethodField()
    slang_usage_choices = serializers.SerializerMethodField()
    typos_frequency_choices = serializers.SerializerMethodField()
    regional_mix_choices = serializers.SerializerMethodField()

    def get_gender_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.GenderChoices.choices
        ]

    def get_age_group_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.AgeGroupChoices.choices
        ]

    def get_location_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.LocationChoices.choices
        ]

    def get_profession_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.ProfessionChoices.choices
        ]

    def get_personality_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.PersonalityChoices.choices
        ]

    def get_communication_style_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.CommunicationStyleChoices.choices
        ]

    def get_accent_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.AccentChoices.choices
        ]

    def get_language_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.LanguageChoices.choices
        ]

    def get_conversation_speed_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.ConversationSpeedChoices.choices
        ]

    def get_tone_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.PersonaToneChoices.choices
        ]

    def get_verbosity_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.PersonaVerbosityChoices.choices
        ]

    def get_punctuation_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.PunctuationChoices.choices
        ]

    def get_emoji_usage_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.EmojiUsageChoices.choices
        ]

    def get_slang_usage_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.StandardUsageChoices.choices
        ]

    def get_typos_frequency_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.TypoLevelChoices.choices
        ]

    def get_regional_mix_choices(self, obj):
        return [
            {"value": choice[0], "label": choice[1]}
            for choice in Persona.StandardUsageChoices.choices
        ]

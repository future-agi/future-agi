import pytest

from simulate.models import Persona
from simulate.serializers.persona import PersonaCreateSerializer, PersonaSerializer


def _create_payload(simulation_type="voice", **overrides):
    """Build a valid creation payload with all required behavioural fields."""
    payload = {
        "name": "Sample persona",
        "description": "A realistic persona",
        "simulation_type": simulation_type,
        "multilingual": False,
        "language": "English",
        "personality": ["Friendly and cooperative"],
        "communication_style": ["Direct and concise"],
        "accent": ["American"],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Personality validation
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestPersonalityValidation:
    def test_rejects_when_personality_is_empty(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(personality=[])
        )

        assert not serializer.is_valid()
        assert "personality" in serializer.errors
        assert "At least one personality trait is required" in str(
            serializer.errors["personality"]
        )

    def test_accepts_single_personality_trait(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(personality=["Confident"])
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["personality"] == ["Confident"]

    def test_accepts_multiple_personality_traits(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                personality=["Confident", "Analytical", "Friendly and cooperative"]
            )
        )

        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["personality"]) == 3


# ---------------------------------------------------------------------------
# Communication style validation
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCommunicationStyleValidation:
    def test_rejects_when_communication_style_is_empty(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(communication_style=[])
        )

        assert not serializer.is_valid()
        assert "communication_style" in serializer.errors
        assert "Communication style is required" in str(
            serializer.errors["communication_style"]
        )

    def test_accepts_single_communication_style(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(communication_style=["Casual and friendly"])
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["communication_style"] == [
            "Casual and friendly"
        ]

    def test_accepts_multiple_communication_styles(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                communication_style=["Direct and concise", "Technical"]
            )
        )

        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["communication_style"]) == 2


# ---------------------------------------------------------------------------
# Accent validation (voice-only)
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAccentValidation:
    def test_rejects_when_accent_is_empty_for_voice(self):
        serializer = PersonaCreateSerializer(data=_create_payload(accent=[]))

        assert not serializer.is_valid()
        assert "accent" in serializer.errors
        assert "Accent is required" in str(serializer.errors["accent"])

    def test_accepts_when_accent_is_empty_for_text(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(simulation_type="text", accent=[])
        )

        assert serializer.is_valid(), serializer.errors

    def test_accepts_single_accent_for_voice(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(accent=["British"])
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["accent"] == ["British"]


# ---------------------------------------------------------------------------
# Combined — all three empty
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestAllFieldsEmpty:
    def test_rejects_with_three_errors_when_all_empty_voice(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                personality=[], communication_style=[], accent=[]
            )
        )

        assert not serializer.is_valid()
        assert "personality" in serializer.errors
        assert "communication_style" in serializer.errors
        assert "accent" in serializer.errors

    def test_rejects_with_two_errors_when_all_empty_text(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                simulation_type="text",
                personality=[],
                communication_style=[],
                accent=[],
            )
        )

        assert not serializer.is_valid()
        assert "personality" in serializer.errors
        assert "communication_style" in serializer.errors
        # Accent not required for text
        assert "accent" not in serializer.errors


# ---------------------------------------------------------------------------
# Happy-path: fully filled forms
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestFullyFilled:
    def test_accepts_fully_filled_voice_persona(self):
        serializer = PersonaCreateSerializer(data=_create_payload())

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["personality"] == ["Friendly and cooperative"]
        assert serializer.validated_data["communication_style"] == ["Direct and concise"]
        assert serializer.validated_data["accent"] == ["American"]

    def test_accepts_fully_filled_text_persona_without_accent(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(simulation_type="text", accent=[])
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["personality"] == ["Friendly and cooperative"]


# ---------------------------------------------------------------------------
# Partial fills — ensure ALL required fields are enforced
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestPartialFillsRejected:
    def test_rejects_personality_only_voice(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                personality=["Confident"],
                communication_style=[],
                accent=[],
            )
        )

        assert not serializer.is_valid()
        assert "communication_style" in serializer.errors
        assert "accent" in serializer.errors
        assert "personality" not in serializer.errors

    def test_rejects_communication_style_only_voice(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                personality=[],
                communication_style=["Direct and concise"],
                accent=[],
            )
        )

        assert not serializer.is_valid()
        assert "personality" in serializer.errors
        assert "accent" in serializer.errors
        assert "communication_style" not in serializer.errors

    def test_rejects_accent_only_voice(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                personality=[],
                communication_style=[],
                accent=["American"],
            )
        )

        assert not serializer.is_valid()
        assert "personality" in serializer.errors
        assert "communication_style" in serializer.errors
        assert "accent" not in serializer.errors

    def test_rejects_missing_accent_for_voice(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                personality=["Confident"],
                communication_style=["Direct and concise"],
                accent=[],
            )
        )

        assert not serializer.is_valid()
        assert "accent" in serializer.errors
        assert "personality" not in serializer.errors
        assert "communication_style" not in serializer.errors

    def test_accepts_missing_accent_for_text(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(
                simulation_type="text",
                personality=["Confident"],
                communication_style=["Direct and concise"],
                accent=[],
            )
        )

        assert serializer.is_valid(), serializer.errors


# ---------------------------------------------------------------------------
# Update serializer — backward compatibility
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestUpdateSerializer:
    def test_rejects_clearing_personality_when_other_fields_empty(self):
        """Updating personality to empty should fail if no other fields have data."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            personality=["Friendly and cooperative"],
            communication_style=[],
            accent=[],
        )

        serializer = PersonaSerializer(
            instance=persona,
            data={"personality": []},
            partial=True,
        )

        assert not serializer.is_valid()
        # After clearing personality, no behavioural setting remains
        assert "personality" in serializer.errors

    def test_rejects_clearing_last_behavioural_field(self):
        """Clearing the only remaining behavioural field should fail."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            personality=["Friendly and cooperative"],
            communication_style=["Direct and concise"],
            accent=[],
        )

        # Try to clear both fields at once
        serializer = PersonaSerializer(
            instance=persona,
            data={"personality": [], "communication_style": []},
            partial=True,
        )

        assert not serializer.is_valid()
        assert "personality" in serializer.errors
        assert "communication_style" in serializer.errors

    def test_allows_replacing_personality_with_new_value(self):
        """Updating personality to a different value should succeed."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            personality=["Friendly and cooperative"],
            communication_style=["Direct and concise"],
            accent=["American"],
        )

        serializer = PersonaSerializer(
            instance=persona,
            data={"personality": ["Confident"]},
            partial=True,
        )

        assert serializer.is_valid(), serializer.errors

    def test_allows_unrelated_partial_update(self):
        """Updating only the name should succeed when existing values are present."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            personality=["Friendly and cooperative"],
            communication_style=["Direct and concise"],
            accent=["American"],
        )

        serializer = PersonaSerializer(
            instance=persona,
            data={"name": "Updated persona"},
            partial=True,
        )

        assert serializer.is_valid(), serializer.errors

    def test_rejects_clearing_accent_on_voice_persona(self):
        """Clearing accent on a voice persona should fail."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            personality=["Friendly and cooperative"],
            communication_style=["Direct and concise"],
            accent=["American"],
        )

        serializer = PersonaSerializer(
            instance=persona,
            data={"accent": []},
            partial=True,
        )

        assert not serializer.is_valid()
        assert "accent" in serializer.errors

    def test_rejects_clearing_communication_style_when_personality_remains(self):
        """Clearing communication_style should fail even if personality is present."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            personality=["Friendly and cooperative"],
            communication_style=["Direct and concise"],
            accent=["American"],
        )

        serializer = PersonaSerializer(
            instance=persona,
            data={"communication_style": []},
            partial=True,
        )

        assert not serializer.is_valid()
        assert "communication_style" in serializer.errors

    def test_text_persona_update_does_not_require_accent(self):
        """Updating a text persona without accent should succeed."""
        persona = Persona(
            name="Existing persona",
            description="Existing persona",
            simulation_type="text",
            personality=["Friendly and cooperative"],
            communication_style=["Direct and concise"],
            accent=[],
        )

        serializer = PersonaSerializer(
            instance=persona,
            data={"name": "Updated text persona"},
            partial=True,
        )

        assert serializer.is_valid(), serializer.errors


# ---------------------------------------------------------------------------
# Non-behavioural validations are unchanged
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestNonBehaviouralValidation:
    def test_name_is_required(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(name="")
        )

        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_description_is_required(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(description="")
        )

        assert not serializer.is_valid()
        assert "description" in serializer.errors

    def test_multilingual_requires_language(self):
        serializer = PersonaCreateSerializer(
            data=_create_payload(multilingual=True, language=[])
        )

        assert not serializer.is_valid()
        assert "language" in serializer.errors

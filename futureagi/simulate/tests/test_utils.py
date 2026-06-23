"""
Unit tests for utility functions in the simulate app.

Tests cover:
- generate_simulator_agent_prompt: Prompt generation for simulator agents
- get_personas_by_language: Language-based persona selection
- Voice mapper utilities
"""

import pytest

try:
    from ee.voice.constants.voice_mapper import (
        ENGLISH_PERSONAS,
        HINDI_PERSONAS,
        get_personas_by_language,
    )
except ImportError:
    pytest.skip("Enterprise Edition required", allow_module_level=True)
from simulate.models import AgentDefinition
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def inbound_agent_definition(db, organization, workspace):
    """Create an inbound agent definition (receives calls)."""
    return AgentDefinition.objects.create(
        agent_name="Support Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Inbound support agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def outbound_agent_definition(db, organization, workspace):
    """Create an outbound agent definition (makes calls)."""
    return AgentDefinition.objects.create(
        agent_name="Sales Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=False,
        description="Outbound sales agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


# ============================================================================
# generate_simulator_agent_prompt Tests
# ============================================================================


@pytest.mark.unit
class TestGenerateSimulatorAgentPrompt:
    """Tests for generate_simulator_agent_prompt utility function."""

    def test_generates_prompt_for_inbound_agent(self, inbound_agent_definition):
        """For inbound agents, simulator makes the call."""
        prompt = generate_simulator_agent_prompt(inbound_agent_definition)

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # For inbound agents, simulator makes the call
        assert "make a call" in prompt.lower()
        assert inbound_agent_definition.agent_name in prompt

    def test_generates_prompt_for_outbound_agent(self, outbound_agent_definition):
        """For outbound agents, simulator receives the call."""
        prompt = generate_simulator_agent_prompt(outbound_agent_definition)

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # For outbound agents, simulator receives the call
        assert "receive a call" in prompt.lower()
        assert outbound_agent_definition.agent_name in prompt

    def test_prompt_contains_persona_placeholder(self, inbound_agent_definition):
        """Prompt should contain {{persona}} placeholder for dynamic substitution."""
        prompt = generate_simulator_agent_prompt(inbound_agent_definition)

        assert "{{persona}}" in prompt

    def test_prompt_contains_situation_placeholder(self, inbound_agent_definition):
        """Prompt should contain {{situation}} placeholder for dynamic substitution."""
        prompt = generate_simulator_agent_prompt(inbound_agent_definition)

        assert "{{situation}}" in prompt

    def test_prompt_describes_customer_role(self, inbound_agent_definition):
        """Prompt should describe the simulator as a customer."""
        prompt = generate_simulator_agent_prompt(inbound_agent_definition)

        assert "customer" in prompt.lower()

    def test_prompt_includes_agent_name(self, inbound_agent_definition):
        """Prompt should include the target agent's name."""
        prompt = generate_simulator_agent_prompt(inbound_agent_definition)

        assert inbound_agent_definition.agent_name in prompt

    def test_prompt_instructs_natural_response(self, inbound_agent_definition):
        """Prompt should instruct the simulator to respond naturally."""
        prompt = generate_simulator_agent_prompt(inbound_agent_definition)

        # Should mention responding naturally or staying in character
        assert "natural" in prompt.lower() or "persona" in prompt.lower()


# ============================================================================
# get_personas_by_language Tests
# ============================================================================


@pytest.mark.unit
class TestGetPersonasByLanguage:
    """Tests for get_personas_by_language utility function."""

    def test_returns_english_personas_for_en(self):
        """English language code should return English personas."""
        personas = get_personas_by_language("en")

        assert personas == ENGLISH_PERSONAS
        assert len(personas) > 0

    def test_returns_english_personas_for_english(self):
        """Full 'english' string should return English personas."""
        personas = get_personas_by_language("english")

        assert personas == ENGLISH_PERSONAS

    def test_returns_hindi_personas_for_hi(self):
        """Hindi language code should return Hindi personas."""
        personas = get_personas_by_language("hi")

        assert personas == HINDI_PERSONAS
        assert len(personas) > 0

    def test_returns_hindi_personas_for_hindi(self):
        """Full 'hindi' string should return Hindi personas."""
        personas = get_personas_by_language("hindi")

        assert personas == HINDI_PERSONAS

    def test_returns_hindi_personas_case_insensitive(self):
        """Hindi detection should be case insensitive."""
        assert get_personas_by_language("HI") == HINDI_PERSONAS
        assert get_personas_by_language("Hindi") == HINDI_PERSONAS
        assert get_personas_by_language("HINDI") == HINDI_PERSONAS

    def test_returns_english_personas_for_none(self):
        """None language should default to English personas."""
        personas = get_personas_by_language(None)

        assert personas == ENGLISH_PERSONAS

    def test_returns_english_personas_for_unknown_language(self):
        """Unknown language should default to English personas."""
        personas = get_personas_by_language("fr")  # French
        assert personas == ENGLISH_PERSONAS

        personas = get_personas_by_language("es")  # Spanish
        assert personas == ENGLISH_PERSONAS

        personas = get_personas_by_language("xx")  # Invalid code
        assert personas == ENGLISH_PERSONAS

    def test_returns_english_personas_for_empty_string(self):
        """Empty string should default to English personas."""
        personas = get_personas_by_language("")

        assert personas == ENGLISH_PERSONAS


# ============================================================================
# Persona Structure Tests
# ============================================================================


@pytest.mark.unit
class TestPersonaStructure:
    """Tests verifying persona data structure."""

    def test_english_personas_have_required_fields(self):
        """English personas should have all required fields."""
        required_fields = ["gender", "name", "age_group", "location"]

        for persona in ENGLISH_PERSONAS:
            for field in required_fields:
                assert (
                    field in persona
                ), f"Missing field '{field}' in persona: {persona.get('name', 'unknown')}"

    def test_hindi_personas_have_required_fields(self):
        """Hindi personas should have all required fields."""
        required_fields = ["gender", "name", "age_group", "location"]

        for persona in HINDI_PERSONAS:
            for field in required_fields:
                assert (
                    field in persona
                ), f"Missing field '{field}' in persona: {persona.get('name', 'unknown')}"

    def test_english_personas_have_diverse_genders(self):
        """English personas should include both male and female genders."""
        genders = {p["gender"] for p in ENGLISH_PERSONAS}

        assert "Male" in genders or "male" in genders
        assert "Female" in genders or "female" in genders

    def test_hindi_personas_have_diverse_genders(self):
        """Hindi personas should include both male and female genders."""
        genders = {p["gender"] for p in HINDI_PERSONAS}

        assert "Male" in genders or "male" in genders
        assert "Female" in genders or "female" in genders

    def test_english_personas_count(self):
        """Should have a reasonable number of English personas."""
        assert len(ENGLISH_PERSONAS) >= 5, "Should have at least 5 English personas"

    def test_hindi_personas_count(self):
        """Should have a reasonable number of Hindi personas."""
        assert len(HINDI_PERSONAS) >= 5, "Should have at least 5 Hindi personas"

    def test_personas_have_unique_names(self):
        """Personas within each language should have unique names."""
        english_names = [p["name"] for p in ENGLISH_PERSONAS]
        assert len(english_names) == len(
            set(english_names)
        ), "English personas should have unique names"

        hindi_names = [p["name"] for p in HINDI_PERSONAS]
        assert len(hindi_names) == len(
            set(hindi_names)
        ), "Hindi personas should have unique names"

    def test_personas_have_valid_age_groups(self):
        """Personas should have valid age group formats."""
        valid_age_patterns = ["18-25", "26-35", "36-45", "46-55", "56-65", "65+", "55+"]

        for persona in ENGLISH_PERSONAS + HINDI_PERSONAS:
            age_group = persona.get("age_group", "")
            # Age group should either be in known patterns or contain a hyphen (range)
            is_valid = (
                age_group in valid_age_patterns or "-" in age_group or "+" in age_group
            )
            assert (
                is_valid
            ), f"Invalid age_group '{age_group}' for persona: {persona.get('name')}"

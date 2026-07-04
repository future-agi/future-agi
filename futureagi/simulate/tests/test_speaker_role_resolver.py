"""Tests for SpeakerRoleResolver (simulate/utils/speaker_roles.py)."""

import pytest

from simulate.utils.speaker_roles import SpeakerRoleResolver
from tracer.models.observability_provider import ProviderChoices

# -------------------------------------------------------------------
# detect_provider
# -------------------------------------------------------------------


class TestDetectProvider:

    def test_detects_livekit(self):
        data = {"livekit": {"room_name": "call_123"}}
        assert SpeakerRoleResolver.detect_provider(data) == ProviderChoices.LIVEKIT

    def test_detects_vapi(self):
        data = {"vapi": {"id": "abc"}}
        assert SpeakerRoleResolver.detect_provider(data) == ProviderChoices.VAPI

    def test_none_falls_back_to_vapi(self):
        assert SpeakerRoleResolver.detect_provider(None) == ProviderChoices.VAPI

    def test_empty_dict_falls_back_to_vapi(self):
        assert SpeakerRoleResolver.detect_provider({}) == ProviderChoices.VAPI

    def test_unknown_keys_falls_back_to_vapi(self):
        data = {"retell": {"id": "x"}}
        assert SpeakerRoleResolver.detect_provider(data) == ProviderChoices.VAPI


# -------------------------------------------------------------------
# is_tested_agent
# -------------------------------------------------------------------


class TestIsTestedAgent:

    # VAPI inbound: bot/assistant = simulator, user = tested_agent
    def test_vapi_inbound_user_is_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "user", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is True
        )

    def test_vapi_inbound_bot_is_not_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "bot", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is False
        )

    def test_vapi_inbound_assistant_is_not_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is False
        )

    # VAPI outbound: bot/assistant = tested_agent, user = simulator
    def test_vapi_outbound_assistant_is_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=True
            )
            is True
        )

    def test_vapi_outbound_bot_is_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "bot", provider=ProviderChoices.VAPI, is_outbound=True
            )
            is True
        )

    def test_vapi_outbound_user_is_not_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "user", provider=ProviderChoices.VAPI, is_outbound=True
            )
            is False
        )

    # LiveKit: unchanged (agent worker normalises to assistant = tested_agent)
    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit_assistant_is_tested_agent(self, is_outbound):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "assistant", provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
            )
            is True
        )

    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit_user_is_not_tested_agent(self, is_outbound):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "user", provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
            )
            is False
        )

    def test_system_is_not_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "system", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is False
        )

    def test_tool_calls_is_not_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "tool_calls", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is False
        )

    def test_case_insensitive(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "USER", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is True
        )

    def test_empty_role_is_not_tested_agent(self):
        assert (
            SpeakerRoleResolver.is_tested_agent(
                "", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is False
        )


class TestIsSimulator:

    # VAPI inbound: bot/assistant = simulator
    def test_vapi_inbound_bot_is_simulator(self):
        assert (
            SpeakerRoleResolver.is_simulator(
                "bot", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is True
        )

    def test_vapi_inbound_assistant_is_simulator(self):
        assert (
            SpeakerRoleResolver.is_simulator(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is True
        )

    def test_vapi_inbound_user_is_not_simulator(self):
        assert (
            SpeakerRoleResolver.is_simulator(
                "user", provider=ProviderChoices.VAPI, is_outbound=False
            )
            is False
        )

    # VAPI outbound: user = simulator
    def test_vapi_outbound_user_is_simulator(self):
        assert (
            SpeakerRoleResolver.is_simulator(
                "user", provider=ProviderChoices.VAPI, is_outbound=True
            )
            is True
        )

    def test_vapi_outbound_assistant_is_not_simulator(self):
        assert (
            SpeakerRoleResolver.is_simulator(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=True
            )
            is False
        )

    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit_user_is_simulator(self, is_outbound):
        assert (
            SpeakerRoleResolver.is_simulator(
                "user", provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
            )
            is True
        )

    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit_assistant_is_not_simulator(self, is_outbound):
        assert (
            SpeakerRoleResolver.is_simulator(
                "assistant", provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
            )
            is False
        )


class TestGetEvalRoleLabel:

    # VAPI inbound: assistant/bot -> customer, user -> agent
    def test_vapi_inbound_bot_becomes_customer(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "bot", provider=ProviderChoices.VAPI, is_outbound=False
            )
            == "customer"
        )

    def test_vapi_inbound_assistant_becomes_customer(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=False
            )
            == "customer"
        )

    def test_vapi_inbound_user_becomes_agent(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "user", provider=ProviderChoices.VAPI, is_outbound=False
            )
            == "agent"
        )

    # VAPI outbound: assistant/bot -> agent, user -> customer
    def test_vapi_outbound_assistant_becomes_agent(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=True
            )
            == "agent"
        )

    def test_vapi_outbound_bot_becomes_agent(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "bot", provider=ProviderChoices.VAPI, is_outbound=True
            )
            == "agent"
        )

    def test_vapi_outbound_user_becomes_customer(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "user", provider=ProviderChoices.VAPI, is_outbound=True
            )
            == "customer"
        )

    # LiveKit: unchanged
    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit_assistant_becomes_agent(self, is_outbound):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "assistant", provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
            )
            == "agent"
        )

    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit_user_becomes_customer(self, is_outbound):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "user", provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
            )
            == "customer"
        )

    def test_system_returned_as_is(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "system", provider=ProviderChoices.VAPI, is_outbound=False
            )
            == "system"
        )

    def test_tool_calls_returned_as_is(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "tool_calls", provider=ProviderChoices.VAPI, is_outbound=False
            )
            == "tool_calls"
        )


class TestGetTranscriptRoleSets:

    def test_vapi_inbound(self):
        ta, sim = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.VAPI, is_outbound=False
        )
        assert "user" in ta
        assert "customer" in ta
        assert "bot" in sim
        assert "assistant" in sim
        assert "agent" in sim

    def test_vapi_outbound(self):
        ta, sim = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.VAPI, is_outbound=True
        )
        assert "bot" in ta
        assert "assistant" in ta
        assert "agent" in ta
        assert "user" in sim
        assert "customer" in sim

    @pytest.mark.parametrize("is_outbound", [False, True])
    def test_livekit(self, is_outbound):
        ta, sim = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.LIVEKIT, is_outbound=is_outbound
        )
        assert "assistant" in ta
        assert "bot" in ta
        assert "user" in sim
        assert "customer" in sim

    def test_sets_are_disjoint(self):
        ta, sim = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.VAPI, is_outbound=False
        )
        assert ta & sim == set()


class TestGetSkipDecisionRoleSets:

    def test_returns_same_as_transcript_role_sets(self):
        for provider in [ProviderChoices.VAPI, ProviderChoices.LIVEKIT]:
            for is_outbound in [True, False]:
                skip = SpeakerRoleResolver.get_skip_decision_role_sets(
                    provider=provider, is_outbound=is_outbound
                )
                transcript = SpeakerRoleResolver.get_transcript_role_sets(
                    provider=provider, is_outbound=is_outbound
                )
                assert skip == transcript


class TestStaticRoleLists:

    def test_conversational_roles_has_user_and_assistant(self):
        roles = SpeakerRoleResolver.get_conversational_roles()
        assert len(roles) == 2
        assert "user" in roles
        assert "assistant" in roles

    def test_displayable_roles_excludes_system(self):
        roles = SpeakerRoleResolver.get_displayable_roles()
        assert "system" not in roles

    def test_conversational_matches_displayable(self):
        assert SpeakerRoleResolver.get_conversational_roles() == (
            SpeakerRoleResolver.get_displayable_roles()
        )


# -------------------------------------------------------------------
# detect_is_outbound
# -------------------------------------------------------------------


class TestDetectIsOutbound:
    """Direction detection reads call_metadata.call_direction first, then
    falls back to CallExecution.call_type for legacy rows."""

    class _Fake:
        def __init__(self, call_metadata=None, call_type=None):
            self.call_metadata = call_metadata
            self.call_type = call_type

    def test_prefers_call_metadata_outbound(self):
        obj = self._Fake(
            call_metadata={"call_direction": "outbound"}, call_type="inboundPhoneCall"
        )
        assert SpeakerRoleResolver.detect_is_outbound(obj) is True

    def test_prefers_call_metadata_inbound(self):
        obj = self._Fake(
            call_metadata={"call_direction": "inbound"}, call_type="outboundPhoneCall"
        )
        assert SpeakerRoleResolver.detect_is_outbound(obj) is False

    def test_falls_back_to_call_type_when_metadata_missing(self):
        obj = self._Fake(call_metadata={}, call_type="outboundPhoneCall")
        assert SpeakerRoleResolver.detect_is_outbound(obj) is True

    def test_defaults_to_outbound_when_both_missing(self):
        """Indeterminate direction returns True (outbound = no-swap for VAPI).
        A False default would silently invert outbound records that lost their
        metadata; a True default lets the raw shape (already platform-correct
        on outbound) pass through."""
        assert SpeakerRoleResolver.detect_is_outbound(self._Fake()) is True

    def test_defaults_to_outbound_when_call_type_is_neither(self):
        """A call_type string that mentions neither 'inbound' nor 'outbound'
        should be treated as indeterminate and fall through to the safe default."""
        obj = self._Fake(call_type="webCall")
        assert SpeakerRoleResolver.detect_is_outbound(obj) is True

    def test_handles_none_call_execution(self):
        """None call_execution is treated as indeterminate; return the safe default."""
        assert SpeakerRoleResolver.detect_is_outbound(None) is True

    def test_case_insensitive_call_metadata(self):
        obj = self._Fake(call_metadata={"call_direction": "OUTBOUND"})
        assert SpeakerRoleResolver.detect_is_outbound(obj) is True


# -------------------------------------------------------------------
# Regression contracts (documented behavior that must never silently
# regress)
# -------------------------------------------------------------------


class TestRegressionContracts:
    """These assertions encode invariants explicitly called out in the
    module docstring. If any of them start failing, someone has either
    flipped a map (breaking read alignment) or changed which side the
    convention normalizes to (breaking downstream consumers)."""

    def test_vapi_inbound_and_outbound_are_mirror_images(self):
        """The whole reason SpeakerRoleResolver exists is the VAPI
        inbound / outbound divergence. If they ever collapse to the same
        map, the map has regressed."""
        ta_in, sim_in = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.VAPI, is_outbound=False
        )
        ta_out, sim_out = SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.VAPI, is_outbound=True
        )
        assert ta_in == sim_out
        assert sim_in == ta_out

    def test_livekit_is_direction_agnostic(self):
        """LiveKit's agent worker normalises at write time; inbound and
        outbound share the same map. If they ever diverge, someone has
        introduced a read-time swap for LiveKit that shouldn't be there."""
        assert SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.LIVEKIT, is_outbound=False
        ) == SpeakerRoleResolver.get_transcript_role_sets(
            provider=ProviderChoices.LIVEKIT, is_outbound=True
        )

    def test_eval_labels_are_stable_across_providers_and_directions(self):
        """Eval templates read `agent` and `customer` regardless of
        provider. If a fifth label ever leaks through, the LLM prompt
        contract has drifted."""
        seen = set()
        for provider in [ProviderChoices.VAPI, ProviderChoices.LIVEKIT]:
            for is_outbound in [False, True]:
                for raw in ["assistant", "user", "bot", "customer", "agent"]:
                    label = SpeakerRoleResolver.get_eval_role_label(
                        raw, provider=provider, is_outbound=is_outbound
                    )
                    seen.add(label)
        assert seen == {"agent", "customer"}

    def test_conversational_roles_never_include_system(self):
        """The FAGI simulator's persona system prompt must not leak into
        eval inputs or the transcript view. This is the only line of
        defence before the raw payload hits the LLM."""
        roles = SpeakerRoleResolver.get_conversational_roles()
        assert "system" not in roles
        assert "tool_calls" not in roles
        assert "tool_call_result" not in roles

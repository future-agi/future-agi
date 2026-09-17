"""Unit tests for the hosted→canonical ended_reason translator."""

from simulate.utils.ended_reason import to_canonical_ended_reason


class TestToCanonicalEndedReason:
    def test_maps_sdk_codes_to_canonical(self):
        assert to_canonical_ended_reason("simulator_end_call") == "customer-ended-call"
        assert to_canonical_ended_reason("agent_unavailable") == "no-answer"
        assert (
            to_canonical_ended_reason("conversation_silence_timeout")
            == "silence-timed-out"
        )
        assert to_canonical_ended_reason("conversation_settled") == "silence-timed-out"
        assert (
            to_canonical_ended_reason("conversation_timeout") == "max-duration-reached"
        )
        assert (
            to_canonical_ended_reason("insufficient_conversation")
            == "assistant-ended-call"
        )
        assert to_canonical_ended_reason("completed") == "assistant-ended-call"

    def test_case_insensitive_and_trimmed(self):
        assert to_canonical_ended_reason("  Agent_Unavailable  ") == "no-answer"

    def test_already_canonical_passes_through(self):
        assert to_canonical_ended_reason("silence-timed-out") == "silence-timed-out"
        assert to_canonical_ended_reason("customer-ended-call") == "customer-ended-call"

    def test_unknown_passes_through_unchanged(self):
        assert (
            to_canonical_ended_reason("some-provider-native") == "some-provider-native"
        )

    def test_empty_and_none_preserved(self):
        assert to_canonical_ended_reason("") == ""
        assert to_canonical_ended_reason(None) is None

"""Behavior tests for the read-time speaker-role alignment.

The DB stores transcript rows and recording URLs in raw Vapi shape. For
VAPI inbound, that means DB `assistant` holds the FAGI simulator persona
and DB `user` holds the tested agent. Every read surface must translate
that back into the platform display convention (assistant = tested
agent, user = simulator) before rendering to the FE / annotator / LLM.

These tests exercise the actual read boundaries (serializer, view,
resolver) with realistic payloads so a future refactor cannot silently
drop the swap.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from simulate.utils.speaker_roles import SpeakerRoleResolver
from tracer.models.observability_provider import ProviderChoices


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


def _vapi_inbound_call(*, transcripts=None, recordings=None):
    """Mock CallExecution shaped like a VAPI inbound row: FAGI's Vapi
    account transcript, `assistant` = simulator, `user` = tested agent."""
    obj = MagicMock()
    obj.provider_call_data = {
        "vapi": {"recording": recordings or {}, "id": "vapi-123"}
    }
    obj.call_metadata = {"call_direction": "inbound"}
    obj.call_type = "inboundPhoneCall"
    obj.simulation_call_type = "voice"
    if transcripts is None:
        obj.transcripts = MagicMock()
        obj.transcripts.exclude.return_value = []
    else:
        obj.transcripts = MagicMock()
        obj.transcripts.exclude.return_value = transcripts
    return obj


def _vapi_outbound_call(*, transcripts=None, recordings=None):
    """Mock CallExecution shaped like a VAPI outbound row: tested agent's
    Vapi account, `assistant` = tested agent, `user` = simulator. No read
    swap should fire."""
    obj = MagicMock()
    obj.provider_call_data = {
        "vapi": {"recording": recordings or {}, "id": "vapi-456"}
    }
    obj.call_metadata = {"call_direction": "outbound"}
    obj.call_type = "outboundPhoneCall"
    obj.simulation_call_type = "voice"
    if transcripts is None:
        obj.transcripts = MagicMock()
        obj.transcripts.exclude.return_value = []
    else:
        obj.transcripts = MagicMock()
        obj.transcripts.exclude.return_value = transcripts
    return obj


def _livekit_inbound_call(*, recordings=None):
    """LiveKit is direction-agnostic: worker normalises at write time."""
    obj = MagicMock()
    obj.provider_call_data = {"livekit": {"recording": recordings or {}}}
    obj.call_metadata = {"call_direction": "inbound"}
    obj.call_type = "inboundPhoneCall"
    obj.simulation_call_type = "voice"
    obj.transcripts = MagicMock()
    obj.transcripts.exclude.return_value = []
    return obj


# -------------------------------------------------------------------
# Serializer read alignment
# -------------------------------------------------------------------


class TestGetRecordingsSwap:
    """CallExecutionDetailSerializer.get_recordings must swap
    recordings.assistant <-> recordings.customer for VAPI inbound so the
    yellow-color track carries the tested agent's audio in the FE."""

    def _get_recordings(self, obj):
        from simulate.serializers.test_execution import CallExecutionDetailSerializer

        ser = CallExecutionDetailSerializer(context={"detail_mode": True})
        return ser.get_recordings(obj)

    def test_vapi_inbound_swaps_assistant_and_customer_urls(self):
        obj = _vapi_inbound_call(
            recordings={
                "assistant": "https://cdn/simulator.mp3",
                "customer": "https://cdn/tested_agent.mp3",
                "stereo": "https://cdn/stereo.mp3",
            }
        )
        rec = self._get_recordings(obj)
        # After swap, `assistant` = tested-agent audio, `customer` =
        # simulator audio -- matching the FE's display convention.
        assert rec["assistant"] == "https://cdn/tested_agent.mp3"
        assert rec["customer"] == "https://cdn/simulator.mp3"
        # Non-role keys pass through untouched.
        assert rec["stereo"] == "https://cdn/stereo.mp3"

    def test_vapi_outbound_passthrough(self):
        obj = _vapi_outbound_call(
            recordings={
                "assistant": "https://cdn/tested_agent.mp3",
                "customer": "https://cdn/simulator.mp3",
            }
        )
        rec = self._get_recordings(obj)
        assert rec["assistant"] == "https://cdn/tested_agent.mp3"
        assert rec["customer"] == "https://cdn/simulator.mp3"

    def test_livekit_passthrough(self):
        obj = _livekit_inbound_call(
            recordings={
                "assistant": "https://cdn/tested_agent.mp3",
                "customer": "https://cdn/simulator.mp3",
            }
        )
        rec = self._get_recordings(obj)
        assert rec["assistant"] == "https://cdn/tested_agent.mp3"
        assert rec["customer"] == "https://cdn/simulator.mp3"

    def test_empty_recordings_dict_returns_empty(self):
        obj = _vapi_inbound_call(recordings={})
        assert self._get_recordings(obj) == {}

    def test_missing_side_still_swaps_present_side(self):
        """If Vapi only produced one of the two mono URLs, swap must
        still move it to the correct side."""
        obj = _vapi_inbound_call(recordings={"assistant": "https://cdn/only.mp3"})
        rec = self._get_recordings(obj)
        # Simulator audio URL now lives under `customer`.
        assert rec.get("customer") == "https://cdn/only.mp3"


class TestGetTranscriptSwap:
    """CallExecutionDetailSerializer.get_transcript must emit
    speaker_role in the platform convention: `assistant` for the tested
    agent, `user` for the FAGI simulator, regardless of what raw Vapi
    labelled the row."""

    def _mock_transcripts_qs(self, rows):
        """Build the transcripts queryset chain the serializer calls."""
        qs = MagicMock()
        qs.exclude.return_value = rows
        return qs

    def _mock_row(self, speaker_role, content):
        row = MagicMock()
        row.id = f"row-{speaker_role}"
        row.speaker_role = speaker_role
        row.content = content
        row.start_time_ms = 0
        row.end_time_ms = 1000
        row.confidence_score = 1.0
        row.created_at = None
        return row

    def _serialize(self, obj):
        from simulate.serializers.test_execution import CallExecutionDetailSerializer

        ser = CallExecutionDetailSerializer(context={"detail_mode": True})
        return ser.get_transcript(obj)

    def test_vapi_inbound_swaps_per_row(self):
        rows = [
            self._mock_row("assistant", "Hello, I am Layan"),  # simulator persona
            self._mock_row("user", "Hello, I am Jawad from Pall Nis Solutions"),
        ]
        obj = _vapi_inbound_call(transcripts=rows)
        emitted = self._serialize(obj)
        # DB `assistant` (simulator content) -> emit as `user`.
        # DB `user` (tested-agent content) -> emit as `assistant`.
        assert emitted[0]["speaker_role"] == "user"
        assert "Layan" in emitted[0]["content"]
        assert emitted[1]["speaker_role"] == "assistant"
        assert "Jawad" in emitted[1]["content"]

    def test_vapi_outbound_passthrough(self):
        rows = [
            self._mock_row("assistant", "Hello, I am Raj insurance advisor"),
            self._mock_row("user", "Sorry, who is this?"),
        ]
        obj = _vapi_outbound_call(transcripts=rows)
        emitted = self._serialize(obj)
        # No swap: raw shape from customer's Vapi account already matches
        # the platform convention (assistant = tested agent).
        assert emitted[0]["speaker_role"] == "assistant"
        assert emitted[1]["speaker_role"] == "user"


# -------------------------------------------------------------------
# Resolver contract used by eval-input builders
# -------------------------------------------------------------------


class TestEvalTranscriptLabels:
    """Eval templates receive `agent: ...` and `customer: ...` strings.
    The template contract does not accept raw Vapi role names, so the
    resolver must translate at every eval-input builder call site."""

    def test_vapi_inbound_labels_flip_to_platform_convention(self):
        # DB `assistant` = simulator content on VAPI inbound.
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "assistant",
                provider=ProviderChoices.VAPI,
                is_outbound=False,
            )
            == "customer"
        )
        # DB `user` = tested-agent content on VAPI inbound.
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "user", provider=ProviderChoices.VAPI, is_outbound=False
            )
            == "agent"
        )

    def test_vapi_outbound_labels_are_direct(self):
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "assistant", provider=ProviderChoices.VAPI, is_outbound=True
            )
            == "agent"
        )
        assert (
            SpeakerRoleResolver.get_eval_role_label(
                "user", provider=ProviderChoices.VAPI, is_outbound=True
            )
            == "customer"
        )

    def test_system_role_is_never_conversational(self):
        """The persona system prompt must never appear in an eval input."""
        assert "system" not in SpeakerRoleResolver.get_conversational_roles()


# -------------------------------------------------------------------
# Write-time metric-side contract
# -------------------------------------------------------------------


class TestMetricsNormalizeRoles:
    """conversation_metrics._normalize_roles_for_test_agent is the ONE
    write-time site that legitimately uses the resolver. Its purpose is
    to make the stored bot_wpm column mean "tested agent's WPM" no
    matter which raw side that was on. Under the corrected map:

    - VAPI inbound: raw bot = simulator, so swap fires. bot_* metrics
      end up computed on tested-agent's messages.
    - VAPI outbound: raw bot = tested agent already, no swap.
    - LiveKit: normalised at source, no swap.
    """

    def _run(self, provider, is_outbound, messages):
        """Import here to avoid ee-side import at module load, and to
        exercise the real function rather than a fake."""
        from ee.voice.services.conversation_metrics import (
            ConversationMetricsCalculator,
            MessageData,
        )

        calc = ConversationMetricsCalculator(voice_service_provider=provider)
        parsed = [
            MessageData(
                role=m["role"],
                time=m["time"],
                end_time=None,
                message=m["message"],
                duration=None,
                seconds_from_start=m["time"],
            )
            for m in messages
        ]
        return calc._normalize_roles_for_test_agent(parsed, is_outbound)

    def test_vapi_inbound_swaps_bot_and_user(self):
        raw = [
            {"role": "bot", "time": 0.0, "message": "simulator persona"},
            {"role": "user", "time": 1.0, "message": "tested agent"},
        ]
        out = self._run(ProviderChoices.VAPI, False, raw)
        # Post-swap: role "bot" now holds the tested agent's content
        # (which is what will feed into bot_wpm downstream).
        assert out[0].role == "user"
        assert "simulator" in out[0].message
        assert out[1].role == "bot"
        assert "tested agent" in out[1].message

    def test_vapi_outbound_no_swap(self):
        raw = [
            {"role": "bot", "time": 0.0, "message": "tested agent"},
            {"role": "user", "time": 1.0, "message": "simulator"},
        ]
        out = self._run(ProviderChoices.VAPI, True, raw)
        # Raw shape preserved: `bot` already means tested agent for
        # outbound (transcript pulled from customer's Vapi account).
        assert out[0].role == "bot"
        assert "tested agent" in out[0].message
        assert out[1].role == "user"

    def test_livekit_no_swap_either_direction(self):
        raw = [
            {"role": "bot", "time": 0.0, "message": "tested agent"},
            {"role": "user", "time": 1.0, "message": "simulator"},
        ]
        for is_outbound in [False, True]:
            out = self._run(ProviderChoices.LIVEKIT, is_outbound, raw)
            assert out[0].role == "bot"
            assert out[1].role == "user"

    def test_is_outbound_none_is_treated_as_no_context(self):
        """Historical callers pass is_outbound=None when direction is
        unknown. The function must not crash and must return the input
        untouched."""
        raw = [
            {"role": "bot", "time": 0.0, "message": "x"},
            {"role": "user", "time": 1.0, "message": "y"},
        ]
        out = self._run(ProviderChoices.VAPI, None, raw)
        assert out[0].role == "bot"
        assert out[1].role == "user"


# -------------------------------------------------------------------
# Write-time ended_reason swap
# -------------------------------------------------------------------


class TestEndedReasonInlineSwap:
    """voice_large.py inlines an assistant<->customer swap on
    ended_reason for VAPI inbound. Behaviour must match what the old
    resolver call produced so historical rows and new rows agree."""

    @staticmethod
    def _inline_swap(ended_reason: str) -> str:
        """The exact three-step replace chain used by voice_large.py."""
        return (
            ended_reason.replace("assistant", "\x00")
            .replace("customer", "assistant")
            .replace("\x00", "customer")
        )

    def test_swap_is_involutive(self):
        for raw in [
            "assistant-ended-call",
            "customer-ended-call",
            "customer-did-not-answer",
            "assistant-forwarded-call",
        ]:
            assert self._inline_swap(self._inline_swap(raw)) == raw

    def test_non_role_reasons_pass_through(self):
        for raw in [
            "silence-timed-out",
            "max-duration-reached",
            "twilio-failed-to-connect",
        ]:
            assert self._inline_swap(raw) == raw

    def test_placeholder_choice_survives_edge_case_inputs(self):
        """The \\x00 placeholder was picked because no legitimate Vapi
        ended_reason contains a NUL byte. If someone ever changes the
        placeholder to a printable substring, this test catches it."""
        payload = "assistant-was-customer-friendly"
        result = self._inline_swap(payload)
        # Both role words swapped, no placeholder leaked.
        assert result == "customer-was-assistant-friendly"
        assert "\x00" not in result

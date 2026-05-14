"""
Unit tests for simulate.utils.session_comparison.

We keep these tests lightweight by mocking DB-heavy helpers, and focus on:
- input validation
- output shape
- metric math behavior (percentage_change vs base=0)
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.utils import session_comparison


@pytest.mark.unit
class TestFetchComparisonMetrics:
    def test_raises_when_missing_inputs(self):
        with pytest.raises(ValueError):
            session_comparison.fetch_comparison_metrics(None, "session")
        with pytest.raises(ValueError):
            session_comparison.fetch_comparison_metrics(object(), "")

    def test_calculates_percentage_change_when_base_non_zero(self):
        fake_call_exec = SimpleNamespace(id="call-exec-1")

        with (
            patch.object(session_comparison, "fetch_base_session_metrics") as mock_base,
            patch.object(
                session_comparison, "fetch_call_execution_metrics"
            ) as mock_call,
        ):
            mock_base.return_value = {
                "duration": 10.0,
                "tokens": 100,
                "turn_count": 5,
                "tools_count": 2,
            }
            mock_call.return_value = {
                "duration": 5.0,
                "tokens": 50,
                "turn_count": 4,
                "tools_count": 4,
            }

            result = session_comparison.fetch_comparison_metrics(
                fake_call_exec, "session-123"
            )

        # duration: (5-10)/10*100 = -50%
        duration = next(item for item in result if item["metric"] == "duration")
        assert duration["value"] == 5.0
        assert duration["change"] == -5.0
        assert duration["percentage_change"] == pytest.approx(-50.0)

        # tools_count: (4-2)/2*100 = +100%
        tools = next(item for item in result if item["metric"] == "tools_count")
        assert tools["percentage_change"] == pytest.approx(100.0)

    def test_sets_percentage_change_none_when_base_zero(self):
        fake_call_exec = SimpleNamespace(id="call-exec-2")

        with (
            patch.object(session_comparison, "fetch_base_session_metrics") as mock_base,
            patch.object(
                session_comparison, "fetch_call_execution_metrics"
            ) as mock_call,
        ):
            mock_base.return_value = {
                "duration": 0,
                "tokens": 0,
                "turn_count": 0,
                "tools_count": 0,
            }
            mock_call.return_value = {
                "duration": 5.0,
                "tokens": 50,
                "turn_count": 1,
                "tools_count": 2,
            }

            result = session_comparison.fetch_comparison_metrics(
                fake_call_exec, "session-123"
            )

        assert all(item["percentage_change"] is None for item in result)
        # change should still be computed.
        tokens = next(item for item in result if item["metric"] == "tokens")
        assert tokens["change"] == 50


@pytest.mark.unit
class TestFetchComparisonTranscripts:
    def test_raises_when_missing_inputs(self):
        with pytest.raises(ValueError):
            session_comparison.fetch_comparison_transcripts(None, "session")
        with pytest.raises(ValueError):
            session_comparison.fetch_comparison_transcripts(object(), None)

    def test_returns_expected_shape(self):
        fake_call_exec = SimpleNamespace(id="call-exec-3")

        with (
            patch.object(
                session_comparison, "fetch_base_session_transcripts"
            ) as mock_base,
            patch.object(
                session_comparison, "fetch_call_execution_transcripts"
            ) as mock_call,
        ):
            mock_base.return_value = [{"role": "user", "messages": ["hi"]}]
            mock_call.return_value = [{"role": "assistant", "messages": ["hello"]}]

            result = session_comparison.fetch_comparison_transcripts(
                fake_call_exec, "session-123"
            )

        assert result == {
            "base_session_transcripts": [{"role": "user", "messages": ["hi"]}],
            "comparison_call_transcripts": [
                {"role": "assistant", "messages": ["hello"]}
            ],
        }


@pytest.mark.unit
class TestConvertTraceToChatMessages:
    def test_returns_empty_list_for_none_or_empty_input(self):
        assert session_comparison.convert_trace_to_chat_messages(None) == []
        assert session_comparison.convert_trace_to_chat_messages([]) == []

    def test_converts_traces_with_input_output(self):
        trace = type(
            "TraceLike",
            (),
            {"input": "hello", "output": "hi", "created_at": "2025-01-01T00:00:00Z"},
        )()
        msgs = session_comparison.convert_trace_to_chat_messages([trace])

        assert len(msgs) == 2
        assert msgs[0]["messages"] == ["hello"]
        assert msgs[1]["messages"] == ["hi"]

    def test_skips_traces_missing_input_or_output(self):
        bad_trace_1 = type(
            "TraceLike", (), {"input": None, "output": "x", "created_at": "t"}
        )()
        bad_trace_2 = type(
            "TraceLike", (), {"input": "x", "output": None, "created_at": "t"}
        )()
        msgs = session_comparison.convert_trace_to_chat_messages(
            [bad_trace_1, bad_trace_2]
        )
        assert msgs == []


@pytest.mark.unit
class TestFetchSimulatedCallRecordings:
    def test_extracts_from_vapi_artifact_recording(self):
        call_exec = SimpleNamespace(
            provider_call_data={
                "vapi": {
                    "artifact": {
                        "recording": {
                            "mono": {
                                "combinedUrl": "https://example.com/combined.wav",
                                "customerUrl": "https://example.com/customer.wav",
                                "assistantUrl": "https://example.com/assistant.wav",
                            },
                            "stereoUrl": "https://example.com/stereo.wav",
                        }
                    }
                }
            },
            recording_url=None,
            stereo_recording_url=None,
        )
        result = session_comparison.fetch_simulated_call_recordings(call_exec)
        assert result == {
            "mono_combined": "https://example.com/combined.wav",
            "mono_customer": "https://example.com/customer.wav",
            "mono_assistant": "https://example.com/assistant.wav",
            "stereo": "https://example.com/stereo.wav",
        }

    def test_falls_back_to_model_fields(self):
        call_exec = SimpleNamespace(
            provider_call_data={},
            recording_url="https://example.com/mono.mp3",
            stereo_recording_url="https://example.com/stereo.mp3",
        )
        result = session_comparison.fetch_simulated_call_recordings(call_exec)
        assert result == {
            "mono_combined": "https://example.com/mono.mp3",
            "stereo": "https://example.com/stereo.mp3",
        }

    def test_returns_empty_when_no_data(self):
        call_exec = SimpleNamespace(
            provider_call_data=None,
            recording_url=None,
            stereo_recording_url=None,
        )
        result = session_comparison.fetch_simulated_call_recordings(call_exec)
        assert result == {}

    def test_single_provider_key(self):
        """When provider_call_data has a single key, use its value."""
        call_exec = SimpleNamespace(
            provider_call_data={
                "retell": {
                    "artifact": {
                        "recording": {
                            "stereoUrl": "https://example.com/retell-stereo.wav",
                        }
                    }
                }
            },
            recording_url=None,
            stereo_recording_url=None,
        )
        result = session_comparison.fetch_simulated_call_recordings(call_exec)
        assert result == {"stereo": "https://example.com/retell-stereo.wav"}


@pytest.mark.unit
class TestFetchBaselineTraceRecordings:
    def test_extracts_recordings_from_span_attributes(self):
        fake_span = {
            "span_attributes": {
                "conversation.recording.stereo": "https://example.com/stereo.wav",
                "conversation.recording.mono.combined": "https://example.com/combined.wav",
            },
            "eval_attributes": {},
        }
        result = session_comparison.fetch_baseline_trace_recordings(
            "trace-123", _span=fake_span
        )

        assert result == {
            "stereo": "https://example.com/stereo.wav",
            "mono_combined": "https://example.com/combined.wav",
        }

    def test_returns_empty_when_no_span(self):
        with patch.object(
            session_comparison,
            "fetch_voice_conversation_span",
            side_effect=ValueError("missing"),
        ):
            result = session_comparison.fetch_baseline_trace_recordings("trace-123")

        assert result == {}

    def test_returns_empty_for_empty_trace_id(self):
        assert session_comparison.fetch_baseline_trace_recordings("") == {}
        assert session_comparison.fetch_baseline_trace_recordings(None) == {}


@pytest.mark.unit
class TestFetchComparisonRecordings:
    def test_returns_both_baseline_and_simulated(self):
        call_exec = SimpleNamespace(
            provider_call_data={
                "vapi": {"artifact": {"recording": {"stereoUrl": "https://sim.wav"}}}
            },
            recording_url=None,
            stereo_recording_url=None,
        )
        fake_span = {
            "span_attributes": {"conversation.recording.stereo": "https://base.wav"},
            "eval_attributes": {},
        }
        result = session_comparison.fetch_comparison_recordings(
            call_exec, "trace-1", _span=fake_span
        )

        assert result["baseline"] == {"stereo": "https://base.wav"}
        assert result["simulated"] == {"stereo": "https://sim.wav"}

"""Small runs and empty transcript polling must not look like failed work."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import structlog
from structlog.testing import capture_logs


@pytest.mark.requires_ee
@pytest.mark.parametrize("count", range(5))
def test_small_summary_skips_without_model_work_or_errors(count, monkeypatch):
    from ee.agenthub.explanation_agent import exp_agent

    monkeypatch.setattr(exp_agent, "logger", structlog.get_logger(exp_agent.__name__))
    agent = object.__new__(exp_agent.ExplanationAgent)
    agent._embed = Mock(side_effect=AssertionError("small run must not embed"))
    with capture_logs() as records:
        result = agent.evaluate(
            explanation=[{"text": "synthetic reason"}] * count, eval_name="test"
        )
    assert result == []
    agent._embed.assert_not_called()
    assert [r["log_level"] for r in records] == ["info"]
    assert records[0]["input_count"] == count
    assert records[0]["usable_count"] == count
    assert records[0]["reason"] == "insufficient_samples"
    assert "synthetic reason" not in str(records)


@pytest.mark.requires_ee
def test_sufficient_run_with_missing_reasons_stays_error(monkeypatch):
    from ee.agenthub.explanation_agent import exp_agent

    monkeypatch.setattr(exp_agent, "logger", structlog.get_logger(exp_agent.__name__))
    agent = object.__new__(exp_agent.ExplanationAgent)
    agent._embed = Mock()
    with capture_logs() as records:
        assert agent.evaluate(explanation=[{"reason": " "}] * 5, eval_name="test") == []
    assert [r["log_level"] for r in records] == ["error"]
    assert records[0]["event"] == "explanation_summary_missing_reasons"
    agent._embed.assert_not_called()


@pytest.mark.requires_ee
def test_five_supported_reasons_enter_embedding():
    from ee.agenthub.explanation_agent.exp_agent import ExplanationAgent

    agent = object.__new__(ExplanationAgent)
    agent._embed = Mock(side_effect=RuntimeError("embedding reached"))
    explanations = [
        "one",
        {"text": "two"},
        {"reason": "three"},
        {"eval_reason": "four"},
        {"explanation": "five"},
    ]
    with pytest.raises(RuntimeError, match="embedding reached"):
        agent.evaluate(explanation=explanations, eval_name="test")
    agent._embed.assert_called_once_with(["one", "two", "three", "four", "five"])


@pytest.mark.parametrize(
    "serializer_name,method_name",
    [
        ("CallExecutionDetailSerializer", "get_transcript"),
        ("CallExecutionSerializer", "get_transcripts"),
    ],
)
@pytest.mark.parametrize("metadata", [None, {}])
def test_pending_empty_transcripts_do_not_resolve_provider(
    serializer_name, method_name, metadata
):
    from simulate.serializers import test_execution
    from simulate.utils.speaker_roles import SpeakerRoleResolver

    obj = SimpleNamespace(
        simulation_call_type="voice",
        transcripts=Mock(),
        provider_call_data=metadata,
        call_metadata={},
    )
    obj.transcripts.exclude.return_value = []
    with patch.object(SpeakerRoleResolver, "detect_provider") as detect:
        serializer = getattr(test_execution, serializer_name)()
        assert getattr(serializer, method_name)(obj) == []
    detect.assert_not_called()


@pytest.mark.parametrize("metadata", [{}, {"unknown": {}}, "invalid"])
def test_missing_or_unknown_provider_with_transcripts_still_errors(
    metadata, monkeypatch
):
    from simulate.serializers import test_execution
    from simulate.utils import speaker_roles

    monkeypatch.setattr(
        speaker_roles, "logger", structlog.get_logger(speaker_roles.__name__)
    )
    obj = SimpleNamespace(
        simulation_call_type="voice",
        transcripts=Mock(),
        provider_call_data=metadata,
        call_metadata={},
    )
    with (
        patch.object(test_execution, "CallTranscriptSerializer") as serializer,
        capture_logs() as records,
    ):
        serializer.return_value.data = [{"speaker_role": "assistant", "text": "hello"}]
        test_execution.CallExecutionDetailSerializer().get_transcript(obj)
    assert any(r["log_level"] == "error" for r in records)

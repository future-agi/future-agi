"""Declared tested-agent language: persisted snapshots, cohorts and privacy.

Numeric tests use explicitly synthetic calibration and fake ONNX/VAD seams;
they test plumbing, not perceptual accuracy or real-model qualification.
"""

import io
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from ee.voice.services import audio_storage
from ee.voice.services.audio_analysis import decode, pitch_snr, vqi
from ee.voice.services.audio_analysis.envelope import metric_entry
from ee.voice.temporal.activities import audio_analysis as aa
from ee.voice.tests.audio_analysis import test_schedule
from ee.voice.tests.audio_analysis.test_calibration import MODEL_SHA, load
from ee.voice.tests.audio_analysis.test_vqi_windows import Detector, Session
from simulate.models.agent_version import AgentVersion
from simulate.serializers.test_execution import CallExecutionDetailSerializer

audio_call = test_schedule.audio_call
eligible_call = test_schedule.eligible_call
schedule = test_schedule.schedule


@pytest.mark.parametrize(
    "language,languages,expected",
    [
        ("EN", ["en", "EN"], "en"),
        ("en", None, "en"),
        ("en", [], "en"),
        (None, ["EN"], "en"),
        ("", ["en"], "en"),
        (" en ", ["EN", ""], "en"),
        (None, None, None),
        ("", [], None),
        ("en", ["fr"], None),
        (None, ["en", "fr"], None),
        ("en-US", ["en"], None),
        ("eng", [], None),
        ("12", [], None),
        ("éa", [], None),
        (True, [], None),
        ("en", "en", None),
        ("en", [1], None),
    ],
)
def test_single_declared_language(language, languages, expected):
    assert aa._single_declared_language(language, languages) == expected


def make_version(call, snapshot, number):
    agent = call.test_execution.agent_definition
    return AgentVersion.objects.create(
        agent_definition=agent,
        organization_id=agent.organization_id,
        workspace_id=agent.workspace_id,
        version_number=number,
        configuration_snapshot=snapshot,
        commit_message="Synthetic language test version",
        description="Synthetic language test version",
    )


@pytest.mark.parametrize("source", ["call", "execution", "run", "definition"])
def test_persisted_language_source(eligible_call, source):
    call = eligible_call
    execution = call.test_execution
    run = execution.run_test
    agent = execution.agent_definition
    agent.language = "fr"
    agent.languages = ["fr"]
    agent.save(update_fields=["language", "languages"])
    if source != "definition":
        version = make_version(call, {"language": "EN", "languages": ["en"]}, 1)
        owner = {"call": call, "execution": execution, "run": run}[source]
        owner.agent_version = version
        owner.save(update_fields=["agent_version"])
    scheduled = schedule(call)
    persisted = aa._load_current(
        SimpleNamespace(
            call_id=str(call.id), generation=0, analysis_id=scheduled.analysis_id
        )
    )
    assert aa._declared_language(persisted) == (
        None if source == "definition" else "en"
    )


def test_call_snapshot_wins_over_execution_and_run(eligible_call):
    call = eligible_call
    call.agent_version = make_version(call, {"language": "en"}, 1)
    call.test_execution.agent_version = make_version(call, {"language": "fr"}, 2)
    call.test_execution.run_test.agent_version = make_version(
        call, {"language": "de"}, 3
    )
    assert aa._declared_language(call) == "en"
    call.agent_version = None
    assert aa._declared_language(call) == "fr"


@pytest.mark.parametrize(
    "snapshot", [{}, None, [], {"language": "en", "languages": ["fr"]}]
)
def test_ambiguous_snapshot_never_falls_back_to_definition(eligible_call, snapshot):
    agent = eligible_call.test_execution.agent_definition
    agent.language = "en"
    eligible_call.agent_version = make_version(eligible_call, {}, 1)
    eligible_call.agent_version.configuration_snapshot = snapshot
    assert aa._declared_language(eligible_call) is None


@pytest.mark.parametrize("source", ["version", "definition"])
def test_cross_org_configuration_is_not_language_evidence(eligible_call, source):
    import uuid

    if source == "version":
        version = make_version(eligible_call, {"language": "en"}, 1)
        version.organization_id = uuid.uuid4()
        eligible_call.agent_version = version
    else:
        agent = eligible_call.test_execution.agent_definition
        agent.language = "en"
        agent.organization_id = uuid.uuid4()
    assert aa._declared_language(eligible_call) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "language,languages,origin,expected_reason",
    [
        ("EN", ["en"], "provider_received_track", None),
        ("en", ["fr"], "provider_received_track", "out_of_domain"),
        (None, [], "provider_received_track", "out_of_domain"),
        ("en", [], "provider_source_track", "out_of_domain"),
    ],
)
def test_activity_uses_persisted_language_and_keeps_it_private(
    eligible_call,
    settings,
    monkeypatch,
    tmp_path,
    language,
    languages,
    origin,
    expected_reason,
):
    call = eligible_call
    call.agent_version = make_version(
        call, {"language": language, "languages": languages}, 1
    )
    if origin == "provider_source_track":
        call.audio_provenance.update(
            direction="outbound",
            recording_owner_account="client",
            capture_origin=origin,
        )
        call.audio_provenance["artifacts"][0]["provider_role"] = "assistant"
    call.save(update_fields=["agent_version", "audio_provenance"])
    settings.VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH = "/synthetic/model"
    settings.VOICE_AUDIO_METRICS_CALIBRATION_PATH = "/synthetic/calibration"
    result = schedule(call)
    assert result.scheduled
    input = aa.AnalyzeCallAudioInput(
        str(call.id), result.analysis_id, result.generation, result.deadline_at
    )
    samples = (0.1 * np.sin(2 * np.pi * 220 * np.arange(144160) / 16000)).astype(
        "float32"
    )
    encoded = io.BytesIO()
    sf.write(encoded, samples, 16000, format="WAV", subtype="FLOAT")
    monkeypatch.setattr(audio_storage, "get_audio_storage", Mock(return_value=Mock()))
    monkeypatch.setattr(decode, "fetch_audio", Mock(return_value=encoded.getvalue()))
    monkeypatch.setattr(
        pitch_snr,
        "analyze_pitch_snr",
        lambda *args: (
            metric_entry("average_pitch_hz", state="available", value=220),
            metric_entry("estimated_snr_db", state="available", value=10),
        ),
    )
    session = Session()
    monkeypatch.setattr(
        vqi,
        "analyze_vqi",
        partial(
            vqi.analyze_vqi,
            registry=Mock(get=Mock(return_value=(session, MODEL_SHA))),
            detector=Detector([(0, 9.01)]),
            calibration=load(tmp_path),
        ),
    )
    outcome = aa._analyze(input, lambda _: None)
    assert outcome.committed
    call.refresh_from_db()
    entry = call.audio_metrics["metrics"]["voice_quality_index"]
    assert entry["reason"] == expected_reason
    if expected_reason is None:
        assert entry["state"] == "available"
        assert entry["value"] == pytest.approx(
            5 * ((-0.06766283 * 9 + 1.11546468 * 3 + 0.04602535) - 1) / 4
        )
        assert len(session.windows) == 1
    else:
        assert entry["value"] is None
        assert not session.windows
    assert call.audio_provenance["declared_language"] == (
        "en" if language in {"en", "EN"} and "fr" not in languages else None
    )
    assert "language" not in call.audio_metrics["source"]
    assert "declared_language" not in call.audio_metrics["source"]
    public = CallExecutionDetailSerializer(
        call, context={"include_audio_metrics": True}
    ).data
    assert "audio_provenance" not in public
    assert "declared_language" not in public["audio_metrics"]["source"]


def test_language_changed_during_analysis_rejects_commit(eligible_call):
    from ee.voice.services.audio_analysis.errors import StaleAnalysisError

    call = eligible_call
    call.agent_version = make_version(call, {"language": "en"}, 1)
    call.save(update_fields=["agent_version"])
    scheduled = schedule(call)
    call.refresh_from_db()
    input = aa.AnalyzeCallAudioInput(
        str(call.id), scheduled.analysis_id, 0, scheduled.deadline_at
    )
    call.agent_version = make_version(call, {"language": "fr"}, 2)
    call.save(update_fields=["agent_version"])
    with pytest.raises(StaleAnalysisError, match="stale_identity"):
        aa._commit_result(
            input,
            call.audio_metrics,
            provenance=call.audio_provenance,
            decoded=SimpleNamespace(),
            declared_language="en",
        )
    call.refresh_from_db()
    assert call.audio_metrics["state"] == "pending"

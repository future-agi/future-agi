"""R11-R13, AC11-AC13: worker retry, source and failure boundaries."""

import copy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from temporalio.testing import ActivityEnvironment

from ee.voice.services.audio_analysis.envelope import empty_envelope, metric_entry
from ee.voice.services.audio_analysis.errors import (
    AudioDeterministicError,
    StaleAnalysisError,
)
from ee.voice.services.audio_provenance import ProvenanceArtifact, build_vapi_provenance
from ee.voice.temporal.activities import audio_analysis as aa


@pytest.fixture
def worker_case(monkeypatch, settings):
    settings.UPLOAD_BUCKET_NAME = "recordings"
    env = empty_envelope()
    env.update(analysis_id="analysis", state="pending")
    for entry in env["metrics"].values():
        entry.update(state="pending", reason=None)
    env["metrics"]["voice_quality_index"].update(
        state="unavailable", reason="model_unavailable"
    )
    prov = asdict(
        build_vapi_provenance(
            call_id="call",
            direction="inbound",
            recording_owner_account="system",
            artifacts=[
                ProvenanceArtifact(
                    "customer", "call-recordings/call/customer.wav", '"etag"'
                )
            ],
        )
    )
    call = SimpleNamespace(
        id="call",
        audio_metrics=env,
        audio_provenance=prov,
        test_execution=SimpleNamespace(
            run_test=SimpleNamespace(organization_id="org", workspace_id="ws")
        ),
    )
    monkeypatch.setattr(aa, "_load_current", lambda _: copy.deepcopy(call))
    monkeypatch.setattr(aa, "_identity", lambda _: "analysis")
    commit = Mock(side_effect=lambda _input, envelope, **kw: aa._output(envelope, True))
    monkeypatch.setattr(aa, "_commit_result", commit)
    from ee.voice.services import audio_storage

    monkeypatch.setattr(audio_storage, "get_audio_storage", Mock(return_value=Mock()))
    from ee.voice.services.audio_analysis import decode, pitch_snr, vqi

    fetch = Mock(return_value=b"encoded audio")
    monkeypatch.setattr(decode, "fetch_audio", fetch)
    monkeypatch.setattr(
        decode,
        "decode_audio",
        Mock(
            return_value=SimpleNamespace(
                waveform=np.ones(16000, dtype=np.float32),
                sample_rate_hz=16000,
                original_channel_count=1,
                sha256="digest",
                codec="wav",
            )
        ),
    )
    monkeypatch.setattr(
        pitch_snr,
        "analyze_pitch_snr",
        Mock(
            return_value=(
                metric_entry("average_pitch_hz", state="available", value=220),
                metric_entry("estimated_snr_db", state="available", value=0),
            )
        ),
    )
    vqi_mock = Mock(side_effect=AssertionError("Terminal VQI must not run"))
    monkeypatch.setattr(vqi, "analyze_vqi", vqi_mock)
    input = aa.AnalyzeCallAudioInput(
        "call",
        "analysis",
        0,
        (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "org",
        "ws",
    )
    return SimpleNamespace(
        call=call, input=input, commit=commit, fetch=fetch, vqi=vqi_mock
    )


@pytest.mark.asyncio
async def test_activity_heartbeats_and_terminal_vqi_not_upgraded(worker_case):
    stages = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: stages.extend(
        d["stage"] for d in details if isinstance(d, dict)
    )
    result = await env.run(aa.analyze_call_audio, worker_case.input)
    assert result.committed and result.state == "partial"
    assert {"fetch", "decode", "pitch", "snr", "vqi", "commit"} <= set(stages)
    worker_case.vqi.assert_not_called()
    assert (
        worker_case.commit.call_args.args[1]["source"]["target_sample_count"] == 16000
    )


@pytest.mark.parametrize(
    "kind,reason",
    [("tenant", "analysis_error"), ("key", "analysis_error"), ("deadline", "timeout")],
)
@pytest.mark.asyncio
async def test_worker_blocks_fetch_before_authorization(worker_case, kind, reason):
    if kind == "tenant":
        worker_case.input.org_id = "wrong-org"
    elif kind == "key":
        worker_case.call.audio_provenance["artifacts"][0]["object_key"] = (
            "call-recordings/other/customer.wav"
        )
    else:
        worker_case.input.attempt_deadline_at = "2000-01-01T00:00:00+00:00"
    result = await ActivityEnvironment().run(aa.analyze_call_audio, worker_case.input)
    assert result.reasons["average_pitch_hz"] == reason
    worker_case.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_deterministic_fetch_failure_is_terminal(worker_case):
    worker_case.fetch.side_effect = AudioDeterministicError("provider_access_denied")
    result = await ActivityEnvironment().run(aa.analyze_call_audio, worker_case.input)
    assert result.state == "failed"
    assert result.reasons["average_pitch_hz"] == "provider_access_denied"
    assert result.reasons["voice_quality_index"] == "model_unavailable"


@pytest.mark.asyncio
async def test_retry_after_commit_does_not_fetch_again(worker_case):
    worker_case.call.audio_metrics.update(
        state="partial", computed_at=datetime.now(UTC).isoformat()
    )
    result = await ActivityEnvironment().run(aa.analyze_call_audio, worker_case.input)
    assert not result.committed
    worker_case.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_delete_during_inference_rejects_commit(worker_case):
    worker_case.commit.side_effect = StaleAnalysisError("tombstone")
    result = await ActivityEnvironment().run(aa.analyze_call_audio, worker_case.input)
    assert not result.committed


@pytest.mark.asyncio
async def test_transient_storage_error_is_sanitized_and_retryable(worker_case):
    from botocore.exceptions import ReadTimeoutError
    from temporalio.exceptions import ApplicationError

    worker_case.fetch.side_effect = ReadTimeoutError(
        endpoint_url="https://secret-storage/key?credential=secret"
    )
    with pytest.raises(ApplicationError) as error:
        await ActivityEnvironment().run(aa.analyze_call_audio, worker_case.input)
    assert error.value.type == "ReadTimeoutError"
    assert not error.value.non_retryable
    assert "secret" not in str(error.value)
    worker_case.commit.assert_not_called()

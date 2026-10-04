"""R12, AC12: real database stale-write and isolated-column commits."""

import copy

import pytest

from ee.voice.services.audio_analysis.commit import commit_envelope
from ee.voice.services.audio_analysis.errors import StaleAnalysisError
from ee.voice.services.audio_analysis.identity import compute_analysis_id
from simulate.models.test_execution import CallExecution
from simulate.tests.test_call_execution_audio_metrics_api import (  # noqa: F401
    audio_call as audio_call,
)
from simulate.tests.test_call_execution_audio_metrics_api import (
    phase_a,
)


def test_concurrent_latency_write_not_overwritten(audio_call):
    CallExecution.objects.filter(pk=audio_call.pk).update(avg_agent_latency_ms=456)
    result = phase_a()
    result["metrics"]["average_pitch_hz"]["value"] = 230
    commit_envelope(str(audio_call.id), 0, result["analysis_id"], result)
    audio_call.refresh_from_db()
    assert audio_call.avg_agent_latency_ms == 456
    assert audio_call.audio_metrics == result


@pytest.mark.parametrize(
    "change,reason",
    [
        ("reset", "stale_generation"),
        ("identity", "stale_identity"),
        ("delete", "tombstone"),
        ("soft_delete", "tombstone"),
    ],
)
def test_stale_analysis_rejected(audio_call, change, reason):
    result = copy.deepcopy(audio_call.audio_metrics)
    call_id = str(audio_call.pk)
    if change == "reset":
        audio_call.reset_to_default()
    elif change == "identity":
        CallExecution.objects.filter(pk=audio_call.pk).update(
            audio_metrics={"analysis_id": "new"}
        )
    elif change == "soft_delete":
        CallExecution.objects.filter(pk=audio_call.pk).update(deleted=True)
    else:
        CallExecution.objects.filter(pk=audio_call.pk).delete()
    with pytest.raises(StaleAnalysisError, match=reason):
        commit_envelope(call_id, 0, result["analysis_id"], result)


def test_identity_changes_with_source_or_generation():
    args = dict(  # noqa: C408
        org_id="org",
        workspace_id="ws",
        call_id="call",
        generation=0,
        target_object_key="call-recordings/call/a",
        target_object_version_or_etag="v1",
        manifest_version="vapi_role_v1",
        mapping_version="vapi_role_v1",
        preprocessing_version="native_v1",
        pitch_algorithm_version="pyin_c2c6_v1",
        snr_algorithm_version="energy_pause_v1",
        vqi_model_sha256=None,
        calibration_id=None,
    )
    first = compute_analysis_id(**args)
    assert compute_analysis_id(**args) == first
    for key, value in [
        ("generation", 1),
        ("target_object_version_or_etag", "v2"),
        ("org_id", "other"),
        ("calibration_id", "new"),
    ]:
        assert compute_analysis_id(**{**args, key: value}) != first


def test_retry_after_terminal_commit_does_not_overwrite(audio_call):
    result = phase_a()
    result["computed_at"] = "2026-10-02T00:00:00Z"
    commit_envelope(str(audio_call.id), 0, result["analysis_id"], result)
    retry = copy.deepcopy(result)
    retry["metrics"]["average_pitch_hz"]["value"] = 999
    commit_envelope(str(audio_call.id), 0, result["analysis_id"], retry)
    audio_call.refresh_from_db()
    assert audio_call.audio_metrics == result

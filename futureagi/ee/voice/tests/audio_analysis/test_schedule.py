"""R01-R04/R11-R12, AC01/AC02/AC11: persisted scheduling contracts."""

from dataclasses import asdict
from datetime import datetime

import pytest
from asgiref.sync import async_to_sync

from ee.voice.services.audio_provenance import ProvenanceArtifact, build_vapi_provenance
from ee.voice.temporal.activities.audio_analysis import (
    ScheduleAudioAnalysisInput,
    schedule_audio_analysis,
)
from simulate.tests.test_call_execution_audio_metrics_api import (
    audio_call as audio_call,
)


@pytest.fixture
def eligible_call(audio_call, settings):
    settings.VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH = None
    settings.VOICE_AUDIO_METRICS_CALIBRATION_PATH = None
    audio_call.audio_metrics = None
    audio_call.audio_provenance = asdict(
        build_vapi_provenance(
            call_id=str(audio_call.id),
            direction="inbound",
            recording_owner_account="system",
            artifacts=[
                ProvenanceArtifact(
                    "customer",
                    f"call-recordings/{audio_call.id}/customer.wav",
                    '"etag"',
                )
            ],
        )
    )
    audio_call.save(update_fields=["audio_metrics", "audio_provenance"])
    return audio_call


def schedule(call):
    return async_to_sync(schedule_audio_analysis)(
        ScheduleAudioAnalysisInput(str(call.id))
    )


def test_flag_off_writes_nothing(eligible_call, settings):
    settings.VOICE_AUDIO_METRICS_ENABLED = False
    assert schedule(eligible_call).skip_reason == "flag_off"
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics is None


@pytest.mark.parametrize(
    "kind,reason,state",
    [
        ("chat", "not_applicable", "not_requested"),
        ("denied", "not_enabled", "not_requested"),
        ("null", "no_recording", "unavailable"),
        ("unsupported", "unknown_agent_track", "unavailable"),
    ],
)
def test_schedule_gates(eligible_call, settings, kind, reason, state):
    if kind == "chat":
        eligible_call.simulation_call_type = "text"
    elif kind == "denied":
        settings.VOICE_AUDIO_METRICS_ORG_ALLOWLIST = ["another-org"]
    elif kind == "null":
        eligible_call.audio_provenance = None
    else:
        eligible_call.audio_provenance["target"] = None
        eligible_call.audio_provenance["unsupported_reason"] = reason
    eligible_call.save()
    result = schedule(eligible_call)
    assert not result.scheduled
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics["state"] == state
    assert {m["reason"] for m in eligible_call.audio_metrics["metrics"].values()} == {
        reason
    }


@pytest.mark.parametrize(
    "model,calibration,vqi_state,reason",
    [
        (None, None, "unavailable", "model_unavailable"),
        ("/operator/model", None, "unavailable", "not_validated"),
        ("/operator/model", "/operator/calibration", "pending", None),
    ],
)
def test_pending_envelope_and_duplicate_identity(
    eligible_call, settings, model, calibration, vqi_state, reason
):
    settings.VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH = model
    settings.VOICE_AUDIO_METRICS_CALIBRATION_PATH = calibration
    result = schedule(eligible_call)
    assert result.scheduled
    eligible_call.refresh_from_db()
    env = eligible_call.audio_metrics
    assert env["analysis_id"] == result.analysis_id
    assert env["generation"] == 0
    assert env["schema_version"] == 1
    assert env["state"] == "pending"
    assert (
        datetime.fromisoformat(env["deadline_at"])
        - datetime.fromisoformat(env["scheduled_at"])
    ).total_seconds() == 1800
    for name in ("average_pitch_hz", "estimated_snr_db"):
        assert env["metrics"][name]["state"] == "pending"
        assert env["metrics"][name]["value"] is None
    assert env["metrics"]["voice_quality_index"]["state"] == vqi_state
    assert env["metrics"]["voice_quality_index"]["reason"] == reason
    assert schedule(eligible_call).skip_reason == "duplicate_identity"
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == env


def test_tombstone_not_scheduled(eligible_call):
    eligible_call.deleted = True
    eligible_call.save(update_fields=["deleted"])
    assert schedule(eligible_call).skip_reason == "missing_call"


def test_source_replacement_changes_identity(eligible_call):
    first = schedule(eligible_call)
    eligible_call.refresh_from_db()
    eligible_call.audio_provenance["artifacts"][0]["object_version_or_etag"] = (
        '"replacement"'
    )
    eligible_call.save(update_fields=["audio_provenance"])
    assert schedule(eligible_call).analysis_id != first.analysis_id


def test_tenant_mismatch_never_schedules(eligible_call):
    result = async_to_sync(schedule_audio_analysis)(
        ScheduleAudioAnalysisInput(str(eligible_call.id), org_id="other")
    )
    assert not result.scheduled
    eligible_call.refresh_from_db()
    assert {m["state"] for m in eligible_call.audio_metrics["metrics"].values()} == {
        "failed"
    }

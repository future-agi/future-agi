"""R01–R03, AC01/AC02: explicit ownership and unambiguous tracks only."""

from dataclasses import asdict, replace

import pytest

from ee.voice.services.audio_analysis.errors import AudioProvenanceError
from ee.voice.services.audio_provenance import (
    ProvenanceArtifact,
    build_vapi_provenance,
    unsupported_provenance,
    validate_manifest,
)


def artifact(role="customer", **kwargs):
    return ProvenanceArtifact(
        role, "call-recordings/call/" + role + ".wav", '"etag"', **kwargs
    )


def manifest(**kwargs):
    return build_vapi_provenance(
        call_id="call",
        artifacts=[artifact()],
        direction="inbound",
        recording_owner_account="system",
        **kwargs,
    )


@pytest.mark.parametrize(
    "direction,owner,role",
    [("inbound", "system", "customer"), ("outbound", "client", "assistant")],
)
def test_role_follows_direction_and_ownership(direction, owner, role):
    prov = build_vapi_provenance(
        call_id="call",
        artifacts=[artifact("assistant"), artifact("customer")],
        direction=direction,
        recording_owner_account=owner,
    )
    target = validate_manifest(asdict(prov), "call", "recordings")
    assert target.provider_role == role
    assert prov.target.normalized_role == "tested_agent"
    assert f"owner={owner}" in prov.target.evidence


@pytest.mark.parametrize("role", ["combined", "stereo"])
def test_unproven_track_unknown_agent_track(role):
    prov = build_vapi_provenance(
        call_id="call",
        artifacts=[artifact(role)],
        direction="inbound",
        recording_owner_account="system",
    )
    assert prov.unsupported_reason == "unknown_agent_track"
    with pytest.raises(AudioProvenanceError, match="unknown_agent_track"):
        validate_manifest(prov, "call", "recordings")


def test_stereo_channel_index_follows_verified_evidence():
    prov = build_vapi_provenance(
        call_id="call",
        artifacts=[artifact("stereo", channels=2)],
        direction="inbound",
        recording_owner_account="system",
        stereo_channel_map={"customer": 1, "assistant": 0},
        stereo_evidence="fixture_verified:vapi_stereo_doc",
    )
    assert prov.target.channel_index == 1
    validate_manifest(prov, "call", "recordings", decoded_channels=2)
    with pytest.raises(AudioProvenanceError):
        validate_manifest(prov, "call", "recordings", decoded_channels=1)


def test_conflicting_manifest_same_role():
    prov = manifest()
    prov.artifacts.append(
        replace(artifact(), object_key="call-recordings/call/other.wav")
    )
    with pytest.raises(AudioProvenanceError, match="unknown_agent_track"):
        validate_manifest(prov, "call", "recordings")


@pytest.mark.parametrize("engine", ["livekit", "retell"])
def test_unsupported_engine(engine):
    prov = unsupported_provenance("unknown_agent_track", system_engine=engine)
    assert prov.manifest_version == "unsupported_v1"
    assert prov.target is None


def test_web_bridge_does_not_inherit_role():
    assert manifest(is_web_bridge=True).target is None


@pytest.mark.parametrize(
    "bad",
    [{}, {"manifest_version": "vapi_role_v1", "target": {"artifact_index": -1}}, None],
)
def test_malformed_manifest_fails_closed(bad):
    with pytest.raises(AudioProvenanceError):
        validate_manifest(bad, "call", "recordings")


def test_generated_fixture_target_pitch_and_immutable_bytes(tmp_path):
    from ee.voice.services.audio_analysis.decode import decode_audio
    from ee.voice.services.audio_analysis.pitch_snr import analyze_pitch_snr
    from ee.voice.tests.fixtures.audio.make_audio_fixtures import generate

    root = generate(tmp_path, seconds=2, include_mp3=False)
    data = (root / "AB_stereo_c0A_c1B_16k.wav").read_bytes()
    before = bytes(data)
    decoded = decode_audio(data, channel_index=0, expected_channels=2)
    pitch, _ = analyze_pitch_snr(decoded.waveform, decoded.sample_rate_hz)
    assert pitch["value"] == pytest.approx(220, rel=0.05)
    assert data == before

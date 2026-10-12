"""Internal recording provenance. Never project this manifest through an API."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .audio_analysis.errors import AudioProvenanceError

MAPPING_VERSION_VAPI = "vapi_role_v1"
MANIFEST_UNSUPPORTED = "unsupported_v1"


@dataclass
class ProvenanceArtifact:
    provider_role: str
    object_key: str
    object_version_or_etag: str | None
    sha256: str | None = None
    codec: str | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    sample_count: int | None = None
    channel_index: int | None = None


@dataclass
class ProvenanceTarget:
    normalized_role: str
    artifact_index: int
    channel_index: int | None
    evidence: list[str]
    mapping_version: str


@dataclass
class AudioProvenance:
    manifest_version: str
    system_engine: str = "unknown"
    tested_agent_platform: str | None = None
    transport: str = "sip"
    provider_call_id: str | None = None
    recording_owner_account: str = "system"
    capture_origin: str = "unknown"
    artifacts: list[ProvenanceArtifact] = field(default_factory=list)
    target: ProvenanceTarget | None = None
    unsupported_reason: str | None = None
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    direction: str = "inbound"
    is_web_bridge: bool = False


def unsupported_provenance(reason, **context) -> AudioProvenance:
    return AudioProvenance(
        manifest_version=MANIFEST_UNSUPPORTED, unsupported_reason=reason, **context
    )


def validate_object_key(key, call_id) -> None:
    """Only a plain object key in this call's deployment bucket is accepted."""
    prefix = f"call-recordings/{call_id}/"
    if (
        not isinstance(key, str)
        or not key.startswith(prefix)
        or key == prefix
        or any(c in key for c in ("\\", "%", "?", "#", "\x00", ":"))
        or any(part in {".", "..", ""} for part in key.split("/"))
    ):
        raise AudioProvenanceError("analysis_error")


def build_vapi_provenance(
    *,
    call_id,
    artifacts,
    direction,
    recording_owner_account,
    is_web_bridge=False,
    stereo_channel_map=None,
    stereo_evidence=None,
    **context,
) -> AudioProvenance:
    """Map role files; stereo is disabled unless the caller supplies verified evidence.

    Provider emission and real stereo documentation verification belong to the
    next slice. No inferred channel order is built into this module.
    """
    prov = AudioProvenance(
        MAPPING_VERSION_VAPI,
        system_engine="vapi",
        artifacts=artifacts,
        direction=direction,
        recording_owner_account=recording_owner_account,
        capture_origin="provider_recording",
        is_web_bridge=is_web_bridge,
        **context,
    )
    if is_web_bridge:
        prov.transport = "web_bridge"
    role = {("inbound", "system"): "customer", ("outbound", "client"): "assistant"}.get(
        (direction, recording_owner_account)
    )
    reason = "unknown_agent_track"
    try:
        for artifact in artifacts:
            validate_object_key(artifact.object_key, call_id)
    except AudioProvenanceError:
        reason = "no_recording"
    else:
        if not artifacts:
            reason = "no_recording"
        elif role and not is_web_bridge:
            candidates = [i for i, a in enumerate(artifacts) if a.provider_role == role]
            evidence = [
                "vapi_role_file",
                f"direction={direction}",
                f"owner={recording_owner_account}",
            ]
            channel = None
            if not candidates and stereo_channel_map and stereo_evidence:
                channel = stereo_channel_map.get(role)
                if type(channel) is int and channel in {0, 1}:
                    candidates = [
                        i
                        for i, a in enumerate(artifacts)
                        if a.provider_role == "stereo"
                    ]
                    evidence[0] = stereo_evidence
            if len(candidates) == 1:
                prov.target = ProvenanceTarget(
                    "tested_agent",
                    candidates[0],
                    channel,
                    evidence,
                    MAPPING_VERSION_VAPI,
                )
                return prov
    prov.manifest_version = MANIFEST_UNSUPPORTED
    prov.unsupported_reason = reason
    return prov


def _manifest(raw) -> AudioProvenance:
    if isinstance(raw, AudioProvenance):
        return raw
    if not isinstance(raw, dict):
        raise AudioProvenanceError("unknown_agent_track")
    try:
        raw = dict(raw)
        raw["artifacts"] = [ProvenanceArtifact(**a) for a in raw.get("artifacts", [])]
        raw["target"] = ProvenanceTarget(**raw["target"]) if raw.get("target") else None
        return AudioProvenance(**raw)
    except (TypeError, ValueError, KeyError):
        raise AudioProvenanceError("unknown_agent_track") from None


def validate_manifest(
    prov: AudioProvenance | dict | None, call_id, bucket, *, decoded_channels=None
) -> ProvenanceArtifact:
    prov = _manifest(prov)
    if not isinstance(bucket, str) or not bucket or ":" in bucket or "/" in bucket:
        raise AudioProvenanceError("analysis_error")
    if prov.manifest_version not in {
        MAPPING_VERSION_VAPI,
        "livekit_track_v1",
        MANIFEST_UNSUPPORTED,
    }:
        raise AudioProvenanceError("unknown_agent_track")
    target = prov.target
    if prov.manifest_version == MANIFEST_UNSUPPORTED or target is None:
        reason = (
            prov.unsupported_reason
            if prov.unsupported_reason in {"no_recording", "mixed_speakers"}
            else "unknown_agent_track"
        )
        raise AudioProvenanceError(reason)
    if (
        target.normalized_role != "tested_agent"
        or type(target.artifact_index) is not int
        or not 0 <= target.artifact_index < len(prov.artifacts)
        or not target.evidence
        or target.mapping_version != prov.manifest_version
    ):
        raise AudioProvenanceError("unknown_agent_track")
    artifact = prov.artifacts[target.artifact_index]
    validate_object_key(artifact.object_key, call_id)
    if artifact.provider_role not in {"customer", "assistant", "stereo"}:
        raise AudioProvenanceError("unknown_agent_track")
    competing = {
        a.object_key
        for a in prov.artifacts
        if a.provider_role == artifact.provider_role
    }
    if len(competing) != 1:
        raise AudioProvenanceError("unknown_agent_track")
    if artifact.provider_role == "stereo":
        if type(target.channel_index) is not int or target.channel_index not in {0, 1}:
            raise AudioProvenanceError("unknown_agent_track")
        if (
            artifact.channel_index is not None
            and artifact.channel_index != target.channel_index
        ):
            raise AudioProvenanceError("unknown_agent_track")
    elif target.channel_index is not None:
        raise AudioProvenanceError("unknown_agent_track")
    if prov.manifest_version == MAPPING_VERSION_VAPI:
        expected_role = {
            ("inbound", "system"): "customer",
            ("outbound", "client"): "assistant",
        }.get((prov.direction, prov.recording_owner_account))
        if (
            prov.is_web_bridge
            or expected_role is None
            or artifact.provider_role not in {expected_role, "stereo"}
        ):
            raise AudioProvenanceError("unknown_agent_track")
    if decoded_channels is not None:
        if artifact.channels is not None and artifact.channels != decoded_channels:
            raise AudioProvenanceError("unknown_agent_track")
        if decoded_channels not in {1, 2} or (decoded_channels == 2) != (
            artifact.provider_role == "stereo"
        ):
            raise AudioProvenanceError("unknown_agent_track")
    return artifact

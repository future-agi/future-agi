"""Stable identity shared by scheduling and generation-safe persistence."""

import uuid

NAMESPACE_AUDIO = uuid.UUID("5f9c2b2e-4b1c-4f0a-9d3e-2094a0d10a11")


def compute_analysis_id(
    *,
    org_id,
    workspace_id,
    call_id,
    generation,
    target_object_key,
    target_object_version_or_etag,
    manifest_version,
    mapping_version,
    preprocessing_version,
    pitch_algorithm_version,
    snr_algorithm_version,
    vqi_model_sha256,
    calibration_id,
) -> str:
    parts = [
        org_id,
        workspace_id,
        call_id,
        str(generation),
        target_object_key,
        target_object_version_or_etag or "none",
        manifest_version,
        mapping_version,
        preprocessing_version,
        pitch_algorithm_version,
        snr_algorithm_version,
        vqi_model_sha256 or "none",
        calibration_id or "none",
    ]
    return str(uuid.uuid5(NAMESPACE_AUDIO, "|".join(str(part) for part in parts)))

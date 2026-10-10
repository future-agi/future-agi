"""Versioned public vocabulary and fixed PRD input limits."""

from enum import StrEnum


class MetricState(StrEnum):
    PENDING = "pending"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class EnvelopeState(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


UNAVAILABLE_REASONS = frozenset(
    "not_analyzed not_applicable not_enabled no_recording unknown_agent_track mixed_speakers unsupported_format unsupported_sample_rate limit_exceeded empty_audio no_voice insufficient_audio insufficient_voice insufficient_coverage no_voiced_frames no_noise_floor out_of_domain not_validated model_unavailable other_unavailable".split()
)
FAILED_REASONS = frozenset(
    "invalid_audio nonfinite_output storage_unavailable provider_access_denied timeout analysis_error other_failed".split()
)
PIPELINE_VERSION = "audio_metrics_pipeline_v1"
PITCH_ALGORITHM_VERSION = "pyin_c2c6_v1"
SNR_ALGORITHM_VERSION = "energy_pause_v1"
VQI_ALGORITHM_VERSION = "dnsmos_ovrl_mean_v1"
PREPROCESSING_VERSION = "native_v1"
VQI_PREPROCESSING_VERSION = "resample_soxr_hq_16k_v1"
POLYNOMIAL_VERSION = "dnsmos_regular_poly_591184a"
WINDOW_POLICY_VERSION = "win9.01_hop1_minspeech3_cov80_v1"
SCALE_ID = "normalized_mos_0_5_v1"
MAX_ENCODED_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 600.0
MAX_TARGET_SAMPLES = 28_800_000
MIN_SAMPLE_RATE_HZ = 8000
MAX_SAMPLE_RATE_HZ = 48000
ALLOWED_CHANNELS = frozenset({1, 2})
MIN_DURATION_SECONDS = 1.0
SUPPORTED_SUBTYPES = {
    "WAV": {"PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"},
    "MPEG": {"MPEG_LAYER_III"},
    "MP3": {"MPEG_LAYER_III"},
}


def audio_metrics_enabled_for_org(org_id) -> bool:
    """Shared scheduler/read gate: flag, then plan entitlement, then allowlist.

    Entitlement reuses the voice-simulation plan check (`has_voice_sim`), which
    passes on self-hosted deployments and denies on cloud when the plan lacks
    it. A missing entitlement service denies rather than failing open. An empty
    allowlist then means every entitled organization.
    """
    from django.conf import settings

    if not getattr(settings, "VOICE_AUDIO_METRICS_ENABLED", False):
        return False
    if not _org_entitled_to_voice_sim(org_id):
        return False
    allowlist = getattr(settings, "VOICE_AUDIO_METRICS_ORG_ALLOWLIST", [])
    return not allowlist or str(org_id) in allowlist


def _org_entitled_to_voice_sim(org_id) -> bool:
    try:
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        return False
    try:
        return Entitlements.check_feature(str(org_id or ""), "has_voice_sim").allowed
    except Exception:
        return False

"""The VQI cohort tag reaches the API; unknown coverage keys still do not."""

from ee.voice.services.audio_analysis.envelope import empty_envelope, sanitize_for_api


def test_vqi_cohort_tag_survives_projection_and_secrets_do_not():
    raw = empty_envelope(0)
    raw["metrics"]["voice_quality_index"]["coverage"] = {
        "window_count": 2,
        "cohort": "en/sip/provider_received_track",
        "object_key": "call-recordings/x/customer.wav",
    }
    coverage = sanitize_for_api(raw)["metrics"]["voice_quality_index"]["coverage"]
    assert coverage == {"window_count": 2, "cohort": "en/sip/provider_received_track"}


def test_cohort_is_not_projected_for_pitch_or_snr():
    raw = empty_envelope(0)
    raw["metrics"]["average_pitch_hz"]["coverage"] = {"cohort": "en/sip/x"}
    safe = sanitize_for_api(raw)
    assert "cohort" not in (safe["metrics"]["average_pitch_hz"]["coverage"] or {})

"""The test-only energy detector is independent of canonical pitch/SNR helpers."""

import numpy as np
import pytest

from ee.voice.services.audio_analysis.vad import EnergyActivityDetector


@pytest.mark.parametrize("samples", [0, 3, 399, 400, 16000])
def test_test_only_energy_detector_rejects_silence_and_constant_energy(samples):
    detector = EnergyActivityDetector()
    assert detector.detector_id == "test-only-energy-v1"
    assert detector.detect(np.zeros(samples)) == []
    assert detector.detect(np.full(samples, 0.1)) == []


def test_test_only_energy_detector_keeps_25_10ms_activity_geometry():
    y = np.r_[np.zeros(16000), np.ones(16000)]
    before = y.tobytes()
    spans = EnergyActivityDetector().detect(y)
    # First full 25 ms frame intersecting the step starts at .98 s. No centering.
    assert len(spans) == 1
    assert spans[0] == pytest.approx((0.98, 1.995))
    assert y.tobytes() == before

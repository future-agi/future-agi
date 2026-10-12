"""R06/R07, AC06–AC08: fake-session integration, no model weights."""

import hashlib
import sys
from unittest.mock import Mock

import numpy as np
import pytest

from ee.voice.services.audio_analysis import vqi
from ee.voice.services.audio_analysis.constants import (
    POLYNOMIAL_VERSION,
    WINDOW_POLICY_VERSION,
)
from ee.voice.services.audio_analysis.errors import AudioModelUnavailableError
from ee.voice.services.audio_analysis.vad import SileroVad, WebRtcVad
from ee.voice.tests.audio_analysis.test_calibration import DETECTOR_ID, MODEL_SHA, load


class Detector:
    detector_id = DETECTOR_ID

    def __init__(self, spans):
        self.spans = spans

    def detect(self, y16k):
        return self.spans


class Session:
    def __init__(self, output=3.0):
        self.output = output
        self.windows = []

    def run(self, outputs, inputs):
        assert inputs["input_1"].shape == (1, 144160)
        assert inputs["input_1"].dtype == np.float32
        self.windows.append(inputs["input_1"].copy())
        return [np.array([[4.0, 4.0, self.output]])]


def analyze(tmp_path, seconds=9.01, spans=None, output=3.0, **kwargs):
    session = Session(output)
    registry = Mock()
    registry.get.return_value = (session, MODEL_SHA)
    result = vqi.analyze_vqi(
        np.ones(round(seconds * 16000), dtype=np.float32) * 0.1,
        16000,
        detector=Detector(spans if spans is not None else [(0, seconds)]),
        registry=registry,
        calibration=load(tmp_path),
        language="en",
        transport="sip",
        capture_origin="provider_recording",
        **kwargs,
    )
    return result, session


def test_exact_9p01s_one_window(tmp_path):
    result, session = analyze(tmp_path)
    assert result["state"] == "available"
    expected = 5 * ((-0.06766283 * 9 + 1.11546468 * 3 + 0.04602535) - 1) / 4
    assert result["value"] == pytest.approx(expected)
    assert len(session.windows) == 1
    assert result["coverage"]["polynomial_version"] == POLYNOMIAL_VERSION
    assert result["coverage"]["window_policy_version"] == WINDOW_POLICY_VERSION
    assert result["model_version"] == "dnsmos_sig_bak_ovr:" + MODEL_SHA[:12]


@pytest.mark.parametrize(
    "seconds,spans,reason",
    [
        (9.01 - 1 / 16000, None, "insufficient_voice"),
        (9.01, [(0, 2.9)], "insufficient_voice"),
        (9.01, [], "insufficient_voice"),
        (30, [(0, 3), (20, 22)], "insufficient_coverage"),
    ],
)
def test_window_admission(tmp_path, seconds, spans, reason):
    result, session = analyze(tmp_path, seconds, spans)
    assert result["reason"] == reason
    assert not session.windows


def test_exact_3s_speech_admitted(tmp_path):
    assert analyze(tmp_path, spans=[(0, 3)])[0]["state"] == "available"


def test_crosstalk_flagged_mixed_speakers(tmp_path):
    assert analyze(tmp_path, mixed_speakers=True)[0]["reason"] == "mixed_speakers"


@pytest.mark.parametrize(
    "output,reason", [(float("nan"), "nonfinite_output"), (100, "out_of_domain")]
)
def test_invalid_model_output(tmp_path, output, reason):
    assert analyze(tmp_path, output=output)[0]["reason"] == reason


def test_cohort_not_in_allowlist(tmp_path):
    cal = load(tmp_path)
    result = vqi.analyze_vqi(
        np.ones(144160, dtype=np.float32),
        16000,
        detector=Detector([(0, 9.01)]),
        registry=Mock(get=Mock(return_value=(Session(), MODEL_SHA))),
        calibration=cal,
        language="fr",
        transport="sip",
        capture_origin="provider_recording",
    )
    assert result["reason"] == "out_of_domain"


@pytest.mark.parametrize("sr", [8000, 44100, 48000])
def test_resample_count_and_pause_timestamps(sr):
    y = np.zeros(sr * 3, dtype=np.float32)
    y[sr] = 1
    out, expected = vqi.resample_for_vqi(y, sr)
    assert abs(len(out) - expected) <= 1
    assert abs(np.argmax(out) - 16000) <= 1


def test_missing_or_bad_model_never_creates_session(tmp_path):
    factory = Mock()
    registry = vqi.ModelRegistry(session_factory=factory)
    with pytest.raises(AudioModelUnavailableError):
        registry.get(None, None)
    path = tmp_path / "fake.onnx"
    path.write_bytes(b"fake model")
    with pytest.raises(AudioModelUnavailableError):
        registry.get(str(path), "0" * 64)
    factory.assert_not_called()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert registry.get(str(path), digest) == registry.get(str(path), digest)
    assert factory.call_count == 1


@pytest.mark.parametrize(
    "adapter,module", [(WebRtcVad, "webrtcvad"), (SileroVad, "silero_vad")]
)
def test_vad_missing_dependency_clear_error(monkeypatch, adapter, module):
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(AudioModelUnavailableError, match="model_unavailable"):
        adapter().detect(np.ones(16000, dtype=np.float32))


def test_model_and_calibration_precedence(tmp_path, settings):
    kwargs = {
        "transport": "sip",
        "capture_origin": "provider_recording",
        "detector": Detector([(0, 10)]),
    }
    registry = Mock()
    registry.get.side_effect = AudioModelUnavailableError()
    y = np.ones(160000, dtype=np.float32)
    assert (
        vqi.analyze_vqi(y, 16000, registry=registry, **kwargs)["reason"]
        == "model_unavailable"
    )
    assert (
        vqi.analyze_vqi(
            y, 16000, registry=registry, input_error="invalid_audio", **kwargs
        )["reason"]
        == "invalid_audio"
    )
    registry.get.return_value = (Session(), MODEL_SHA)
    registry.get.side_effect = None
    settings.VOICE_AUDIO_METRICS_CALIBRATION_PATH = None
    assert (
        vqi.analyze_vqi(y, 16000, registry=registry, **kwargs)["reason"]
        == "not_validated"
    )

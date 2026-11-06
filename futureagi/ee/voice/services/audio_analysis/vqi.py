"""DNSMOS polynomial coefficients reproduced from microsoft/DNS-Challenge DNSMOS/dnsmos_local.py, Copyright (c) Microsoft Corporation, MIT License.

Regular DNSMOS OVRL windows with pinned, calibrated, local inference.
"""

import hashlib
import logging
import threading
from pathlib import Path

import numpy as np

from .calibration import load_calibration
from .constants import (
    POLYNOMIAL_VERSION,
    SCALE_ID,
    VQI_ALGORITHM_VERSION,
    VQI_PREPROCESSING_VERSION,
    WINDOW_POLICY_VERSION,
)
from .envelope import metric_entry
from .errors import AudioDeterministicError, AudioModelUnavailableError
from .pitch_snr import interval_seconds, interval_union
from .vad import SileroVad

logger = logging.getLogger(__name__)


def _session(path):
    try:
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        return onnxruntime.InferenceSession(
            path, sess_options=options, providers=["CPUExecutionProvider"]
        )
    except Exception:
        raise AudioModelUnavailableError() from None


class ModelRegistry:
    """One pinned session per configuration per process; injectable in unit tests."""

    def __init__(self, session_factory=None):
        self._factory = session_factory or _session
        self._sessions = {}
        self._lock = threading.Lock()
        self._logged_unavailable = False

    def get(self, path=None, sha256=None):
        from django.conf import settings

        path = path or getattr(settings, "VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH", None)
        sha256 = sha256 or getattr(
            settings, "VOICE_AUDIO_METRICS_DNSMOS_MODEL_SHA256", None
        )
        with self._lock:
            key = (path, sha256)
            if key in self._sessions:
                return self._sessions[key]
            digest_match = False
            try:
                if not path or not sha256:
                    raise AudioModelUnavailableError()
                with Path(path).open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                digest_match = actual == sha256
                if not digest_match:
                    raise AudioModelUnavailableError()
                result = (self._factory(str(path)), actual)
                self._sessions[key] = result
                return result
            except Exception:
                if not self._logged_unavailable:
                    logger.error(
                        "audio_metrics.model_unavailable",
                        extra={"path_set": bool(path), "digest_match": digest_match},
                    )
                    self._logged_unavailable = True
                raise AudioModelUnavailableError() from None


MODEL_REGISTRY = ModelRegistry()


def resample_for_vqi(y, sr):
    import librosa

    expected = round(len(y) * 16000 / sr)
    result = librosa.resample(y, orig_sr=sr, target_sr=16000, res_type="soxr_hq")
    if abs(len(result) - expected) > 1:
        raise AudioDeterministicError("analysis_error")
    return np.asarray(result, dtype=np.float32), expected


def _intersections(a, b):
    return [
        (max(start, left), min(end, right))
        for start, end in a
        for left, right in b
        if min(end, right) > max(start, left)
    ]


WINDOW_SAMPLES = 144160  # 9.01 s at 16 kHz, the upstream DNSMOS input length.
WINDOW_HOP_SAMPLES = 16000
MIN_WINDOW_SPEECH_SECONDS = 3.0
MIN_SPEECH_COVERAGE = 0.80


def prepare_vqi_windows(y, sr, detector):
    """Resample, detect target speech and admit full 9.01 s windows (R07).

    Returns ``(y16, admitted_starts, coverage, reason)``; ``reason`` is
    ``insufficient_voice``/``insufficient_coverage`` when no score may be
    computed. Short audio is never repeated or padded.
    """
    y16, expected = resample_for_vqi(y, sr)
    duration = len(y16) / 16000
    spans = detector.detect(y16)
    if any(
        not np.isfinite([a, b]).all() or a < 0 or b < a or b > duration
        for a, b in spans
    ):
        raise AudioDeterministicError("analysis_error")
    spans = interval_union(spans)
    starts = list(range(0, len(y16) - WINDOW_SAMPLES + 1, WINDOW_HOP_SAMPLES))
    admitted = [
        s
        for s in starts
        if interval_seconds(
            _intersections([(s / 16000, (s + WINDOW_SAMPLES) / 16000)], spans)
        )
        >= MIN_WINDOW_SPEECH_SECONDS - 1e-9
    ]
    covered = interval_seconds(
        _intersections(
            interval_union(
                (s / 16000, (s + WINDOW_SAMPLES) / 16000) for s in admitted
            ),
            spans,
        )
    )
    detected = interval_seconds(spans)
    coverage = {
        "window_count": len(admitted),
        "excluded_window_count": len(starts) - len(admitted),
        "detected_speech_seconds": detected,
        "covered_speech_seconds": covered,
        "expected_sample_count": expected,
        "resampled_sample_count": len(y16),
        "detector_id": detector.detector_id,
        "polynomial_version": POLYNOMIAL_VERSION,
        "window_policy_version": WINDOW_POLICY_VERSION,
        "preprocessing_version": VQI_PREPROCESSING_VERSION,
    }
    reason = None
    if not admitted or detected <= 0:
        reason = "insufficient_voice"
    elif covered / detected < MIN_SPEECH_COVERAGE - 1e-9:
        reason = "insufficient_coverage"
    return y16, admitted, coverage, reason


def mean_adjusted_ovrl(session, y16, admitted):
    """Mean upstream-polynomial OVRL over admitted windows: the calibration input x."""
    values = []
    for start in admitted:
        window = y16[start : start + WINDOW_SAMPLES][np.newaxis, :].astype(np.float32)
        outputs = np.asarray(session.run(None, {"input_1": window})[0])
        if outputs.shape != (1, 3):
            raise AudioDeterministicError("analysis_error")
        if not np.isfinite(outputs).all():
            raise AudioDeterministicError("nonfinite_output")
        ovr = float(outputs[0, 2])
        adjusted = -0.06766283 * ovr**2 + 1.11546468 * ovr + 0.04602535
        if not np.isfinite(adjusted):
            raise AudioDeterministicError("nonfinite_output")
        values.append(adjusted)
    return float(np.mean(values))


def calibration_input(y, sr, *, detector, session):
    """The exact product path to x, without calibration or cohort admission.

    Shared by ``analyze_vqi`` and the offline calibration study so the fitted
    mapping is applied to the same quantity it was fitted on.
    """
    y16, admitted, coverage, reason = prepare_vqi_windows(y, sr, detector)
    if reason:
        return {"x": None, "coverage": coverage, "reason": reason}
    return {
        "x": mean_adjusted_ovrl(session, y16, admitted),
        "coverage": coverage,
        "reason": None,
    }


def cohort_tag(language, transport, capture_origin):
    return f"{language or 'und'}/{transport}/{capture_origin}"


def analyze_vqi(
    y,
    sr,
    *,
    detector=None,
    registry=None,
    calibration=None,
    language=None,
    transport,
    capture_origin,
    mixed_speakers=False,
    input_error=None,
):
    """Run after provenance/decode; an input error overrides model/calibration gates.

    Tests may inject a validated calibration and registry. Production defaults
    always require the operator-pinned files and a version-matched detector.
    """
    from django.conf import settings

    entry = metric_entry(
        "voice_quality_index",
        algorithm_version=VQI_ALGORITHM_VERSION,
        scale_id=SCALE_ID,
    )
    try:
        if input_error:
            raise AudioDeterministicError(input_error)
        if mixed_speakers:
            raise AudioDeterministicError("mixed_speakers")
        session, digest = (registry or MODEL_REGISTRY).get()
        entry["model_version"] = "dnsmos_sig_bak_ovr:" + digest[:12]
        detector = detector or SileroVad()
        detector_id = detector.detector_id
        calibration = calibration or load_calibration(
            getattr(settings, "VOICE_AUDIO_METRICS_CALIBRATION_PATH", None),
            getattr(settings, "VOICE_AUDIO_METRICS_CALIBRATION_SHA256", None),
            model_sha256=digest,
            detector_id=detector_id,
        )
        if calibration.model_sha256 != digest or calibration.detector_id != detector_id:
            raise AudioDeterministicError("not_validated")
        entry["calibration_id"] = calibration.calibration_id
        y16, admitted, coverage, reason = prepare_vqi_windows(y, sr, detector)
        cohort = cohort_tag(language, transport, capture_origin)
        entry["coverage"] = {**coverage, "cohort": cohort}
        if reason:
            raise AudioDeterministicError(reason)
        if capture_origin == "unknown" or cohort not in calibration.cohorts:
            raise AudioDeterministicError("out_of_domain")
        x = mean_adjusted_ovrl(session, y16, admitted)
        entry.update(
            value=calibration.apply(x),
            state="available",
            reason=None,
        )
    except AudioDeterministicError as exc:
        entry.update(value=None, state=exc.state, reason=exc.reason)
    except Exception:
        entry.update(value=None, state="failed", reason="analysis_error")
    return entry

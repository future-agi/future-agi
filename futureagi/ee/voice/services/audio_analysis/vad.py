"""Lazy Phase B adapters. No dependency or model is downloaded at runtime."""

from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

import numpy as np

from .errors import AudioModelUnavailableError
from .pitch_snr import interval_union


class SpeechActivityDetector(Protocol):
    @property
    def detector_id(self) -> str: ...

    def detect(self, y16k: np.ndarray) -> list[tuple[float, float]]: ...


def _version(package):
    try:
        return version(package)
    except PackageNotFoundError:
        raise AudioModelUnavailableError() from None


class WebRtcVad:
    @property
    def detector_id(self):
        return f"webrtcvad:{_version('webrtcvad')}:mode3:frame30ms"

    def detect(self, y16k):
        try:
            import webrtcvad
        except ImportError:
            raise AudioModelUnavailableError() from None
        vad = webrtcvad.Vad(3)
        pcm = (np.clip(y16k, -1, 1) * 32767).astype("<i2")
        spans = []
        for start in range(0, len(pcm) - 480 + 1, 480):
            if vad.is_speech(pcm[start : start + 480].tobytes(), 16000):
                spans.append((start / 16000, (start + 480) / 16000))
        return interval_union(spans)


class SileroVad:
    def __init__(self):
        self._model = None

    @property
    def detector_id(self):
        return f"silero-vad:{_version('silero-vad')}:threshold0.5"

    def detect(self, y16k):
        try:
            import torch
            from silero_vad import get_speech_timestamps, load_silero_vad
        except ImportError:
            raise AudioModelUnavailableError() from None
        # The package loads its bundled asset locally; never torch.hub.
        if self._model is None:
            try:
                self._model = load_silero_vad()
            except (OSError, RuntimeError):
                raise AudioModelUnavailableError() from None
        spans = get_speech_timestamps(
            torch.from_numpy(np.asarray(y16k, dtype=np.float32)),
            self._model,
            sampling_rate=16000,
            threshold=0.5,
            return_seconds=False,
        )
        return [(item["start"] / 16000, item["end"] / 16000) for item in spans]


class EnergyActivityDetector:
    """TEST ONLY: helper energy activity is not a validated speech detector."""

    detector_id = "test-only-energy-v1"

    def detect(self, y16k):
        from ee.voice.services.audio_metrics import (
            _ACTIVITY_MARGIN_DB,
            _NOISE_FLOOR_PERCENTILE,
            _POWER_EPSILON,
            _frame_power,
        )

        if not len(y16k):
            return []
        power = _frame_power(y16k, 16000)
        floor = max(
            float(np.percentile(power, _NOISE_FLOOR_PERCENTILE)), _POWER_EPSILON
        )
        active = power > floor * 10 ** (_ACTIVITY_MARGIN_DB / 10)
        return interval_union(
            (i * 0.01, min(i * 0.01 + 0.025, len(y16k) / 16000))
            for i in np.flatnonzero(active)
        )

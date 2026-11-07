"""Phase B dependency pin and bundled-detector installation smoke test."""

import tomllib
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from ee.voice.services.audio_analysis.vad import SileroVad


def test_silero_is_pinned_in_ml_extra():
    project = Path(__file__).resolve().parents[4] / "pyproject.toml"
    config = tomllib.loads(project.read_text())
    assert "silero-vad==6.2.3" in config["project"]["optional-dependencies"]["ml"]
    assert version("silero-vad") == "6.2.3"


def test_pinned_silero_loads_bundled_model_without_torch_hub(monkeypatch):
    import torch

    hub_load = Mock(side_effect=AssertionError("No runtime model download"))
    monkeypatch.setattr(torch.hub, "load", hub_load)
    detector = SileroVad()
    assert detector.detector_id == "silero-vad:6.2.3:threshold0.5"
    assert detector.detect(np.zeros(16000, dtype=np.float32)) == []
    hub_load.assert_not_called()

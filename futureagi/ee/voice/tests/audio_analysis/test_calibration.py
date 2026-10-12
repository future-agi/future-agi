"""R04/R07/R08: locked calibration gates, version admission and scale."""

import hashlib
import json

import pytest

from ee.voice.services.audio_analysis import calibration
from ee.voice.services.audio_analysis.constants import (
    POLYNOMIAL_VERSION,
    SCALE_ID,
    VQI_PREPROCESSING_VERSION,
    WINDOW_POLICY_VERSION,
)
from ee.voice.services.audio_analysis.errors import AudioDeterministicError

MODEL_SHA = "a" * 64
DETECTOR_ID = "fake-vad:1"


def artifact():
    return {
        "calibration_id": "fixture-cal-1",
        "scale_id": SCALE_ID,
        "model_sha256": MODEL_SHA,
        "polynomial_version": POLYNOMIAL_VERSION,
        "preprocessing_version": VQI_PREPROCESSING_VERSION,
        "detector_id": DETECTOR_ID,
        "window_policy_version": WINDOW_POLICY_VERSION,
        "g": {
            "type": "monotone_pchip",
            "knots_x": [1.0, 3.0, 5.0],
            "knots_y": [1.0, 3.0, 5.0],
        },
        "support": {"x_min": 1.0, "x_max": 5.0},
        "cohorts": [
            {
                "tag": "en/sip/provider_recording",
                "n_heldout": 50,
                "bias": 0.1,
                "rmse": 0.5,
            }
        ],
        "gates": {
            "rmse": 0.5,
            "spearman": 0.7,
            "spearman_lb95": 0.6,
            "pair_rank_acc": 0.9,
        },
        "dataset_manifest_sha256": "b" * 64,
        "created_at": "2026-10-01T00:00:00Z",
    }


def load(tmp_path, raw=None, **kwargs):
    path = tmp_path / "calibration.json"
    data = json.dumps(raw or artifact()).encode()
    path.write_bytes(data)
    return calibration.load_calibration(
        str(path),
        hashlib.sha256(data).hexdigest(),
        model_sha256=MODEL_SHA,
        detector_id=DETECTOR_ID,
        **kwargs,
    )


@pytest.mark.parametrize(
    "key",
    [
        "model_sha256",
        "polynomial_version",
        "preprocessing_version",
        "detector_id",
        "window_policy_version",
        "scale_id",
    ],
)
def test_loader_rejects_each_version_mismatch(tmp_path, key):
    raw = artifact()
    raw[key] = "wrong"
    with pytest.raises(AudioDeterministicError, match="not_validated"):
        load(tmp_path, raw)


@pytest.mark.parametrize(
    "knots", [[1, 4, 3], [1, float("nan"), 5], [0, 3, 5], [1, 3, 6]]
)
def test_g_monotone_and_bounded(tmp_path, knots):
    raw = artifact()
    raw["g"]["knots_y"] = knots
    with pytest.raises(AudioDeterministicError, match="not_validated"):
        load(tmp_path, raw)


@pytest.mark.parametrize("mos,index", [(1, 0), (3, 2.5), (5, 5), (None, None)])
def test_anchor_mapping_and_null_stays_null(mos, index):
    assert calibration.normalize_mos(mos) == index


def test_out_of_support_not_clipped(tmp_path):
    cal = load(tmp_path)
    with pytest.raises(AudioDeterministicError, match="out_of_domain"):
        cal.apply(5.01)
    assert cal.apply(3) == 2.5


@pytest.mark.parametrize(
    "gate,value",
    [
        ("rmse", 0.51),
        ("spearman", 0.69),
        ("spearman_lb95", 0.59),
        ("pair_rank_acc", 0.89),
    ],
)
def test_rejects_failed_validation_gate(tmp_path, gate, value):
    raw = artifact()
    raw["gates"][gate] = value
    with pytest.raises(AudioDeterministicError, match="not_validated"):
        load(tmp_path, raw)


def test_bad_digest_and_missing_file(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(AudioDeterministicError, match="not_validated"):
        calibration.load_calibration(
            str(path), "f" * 64, model_sha256=MODEL_SHA, detector_id=DETECTOR_ID
        )
    path.write_text(json.dumps(artifact()))
    with pytest.raises(AudioDeterministicError, match="not_validated"):
        calibration.load_calibration(
            str(path), "f" * 64, model_sha256=MODEL_SHA, detector_id=DETECTOR_ID
        )

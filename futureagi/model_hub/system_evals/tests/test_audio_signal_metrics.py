from pathlib import Path

import yaml

YAML = Path(__file__).resolve().parent.parent / "function" / "audio_signal_metrics.yaml"


def _evaluate():
    definition = yaml.safe_load(YAML.read_text())
    namespace = {}
    exec(compile(definition["config"]["code"], str(YAML), "exec"), namespace)
    return definition, namespace["evaluate"]


def test_definition_is_visible_audio_code_eval():
    definition, _ = _evaluate()
    assert definition["visible_ui"] is True
    assert definition["offered_in_environments"] is True
    assert definition["config"]["eval_type_id"] == "CustomCodeEval"
    assert definition["config"]["required_keys"] == ["input_audio"]
    assert definition["config"]["param_modalities"]["input_audio"] == ["AUDIO"]


def test_passes_when_pitch_and_snr_meet_thresholds():
    _, evaluate = _evaluate()
    result = evaluate(
        None,
        None,
        None,
        None,
        _audio_average_pitch_hz=220.0,
        _audio_estimated_snr_db=12.0,
    )
    assert result == {
        "score": 1.0,
        "reason": "Pitch: 220.0 Hz (pass, range 80.0-400.0). Estimated SNR: 12.0 dB (pass, minimum 6.0 dB).",
    }


def test_fails_when_pitch_or_snr_is_outside_thresholds():
    _, evaluate = _evaluate()
    result = evaluate(
        None,
        None,
        None,
        None,
        _audio_average_pitch_hz=500.0,
        _audio_estimated_snr_db=2.0,
    )
    assert result["score"] == 0.0
    assert "Pitch: 500.0 Hz (fail" in result["reason"]
    assert "Estimated SNR: 2.0 dB (fail" in result["reason"]


def test_fails_closed_when_preprocessing_cannot_measure_audio():
    _, evaluate = _evaluate()
    result = evaluate(None, None, None, None, _audio_metrics_error="decode failed")
    assert result == {
        "score": 0.0,
        "reason": "Audio metrics unavailable: decode failed",
    }

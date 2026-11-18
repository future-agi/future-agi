import pytest

from agentcc.services.log_ingestion import _compute_cost


@pytest.mark.parametrize(
    ("times", "cost"),
    [
        ({"timestamp": "2026-12-31T23:59:59Z"}, 1.125),
        ({"timestamp": "2026-12-31T23:59:59.999999Z"}, 1.125),
        # The gateway writes nanoseconds; the digits past the microsecond are dropped.
        ({"timestamp": "2026-12-31T23:59:59.999999999Z"}, 1.125),
        ({"timestamp": "2027-01-01T00:00:00Z"}, 2.25),
        # The gateway writes UTC; a time with no zone is read as UTC.
        ({"timestamp": "2027-01-01T00:00:00"}, 2.25),
        ({"timestamp": "2026-12-31T19:00:00-05:00"}, 2.25),
        ({"started_at": "2026-12-31T23:59:59Z"}, 1.125),
        (
            {
                "timestamp": "2027-01-01T00:00:00Z",
                "started_at": "2026-12-31T23:59:59Z",
            },
            2.25,
        ),
    ],
)
def test_compute_cost_prices_at_the_logged_request_time(times, cost):
    entry = {
        "model": "gemini-3.7-flash",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 100_000,
        **times,
    }

    assert _compute_cost(entry) == pytest.approx(cost)


def test_compute_cost_uses_tts_input_characters():
    entry = {
        "model": "tts-1",
        "metadata": {
            "input_characters": "1000",
        },
    }

    assert _compute_cost(entry) == 0.015


def test_compute_cost_uses_stt_audio_seconds():
    entry = {
        "model": "whisper-1",
        "metadata": {
            "audio_seconds": "30",
        },
    }

    assert _compute_cost(entry) == 0.003


def test_compute_cost_does_not_charge_for_zero_generated_images(monkeypatch):
    captured = {}

    def fake_get_model_info(model):
        return {"mode": "image_generation"}

    def fake_calculate_total_cost(model, usage, **_):
        captured.update(usage)
        return {"total_cost": 0}

    monkeypatch.setattr(
        "agentic_eval.core_evals.run_prompt.model_pricing.get_model_info",
        fake_get_model_info,
    )
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_utils.token_count_helper.calculate_total_cost",
        fake_calculate_total_cost,
    )

    entry = {
        "model": "gpt-image-1",
        "metadata": {
            "images_generated": 0,
        },
    }

    _compute_cost(entry)

    assert captured["images_generated"] == 0

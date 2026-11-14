from agentcc.services.log_ingestion import _compute_cost


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

    def fake_calculate_total_cost(model, usage):
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


def _price_from_catalogue_at(monkeypatch, price):
    """Stub the catalogue so a re-price is visible and nothing is looked up."""
    calls = []

    def fake_calculate_total_cost(model, usage):
        calls.append((model, dict(usage)))
        return {"total_cost": price}

    monkeypatch.setattr(
        "agentic_eval.core_evals.run_prompt.model_pricing.get_model_info",
        lambda model: {"mode": "chat"},
    )
    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_utils.token_count_helper.calculate_total_cost",
        fake_calculate_total_cost,
    )
    return calls


def test_compute_cost_keeps_a_provider_reported_zero(monkeypatch):
    calls = _price_from_catalogue_at(monkeypatch, 0.42)
    entry = {
        "cost": 0,
        "metadata": {"cost_source": "provider_reported"},
        "model": "perplexity/sonar",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }

    assert _compute_cost(entry) == 0
    assert calls == []


def test_compute_cost_keeps_a_provider_reported_cost(monkeypatch):
    calls = _price_from_catalogue_at(monkeypatch, 0.42)
    entry = {
        "cost": 0.04271,
        "metadata": {"cost_source": "provider_reported"},
        "model": "perplexity/sonar",
        "prompt_tokens": 12088,
        "completion_tokens": 2743,
    }

    assert _compute_cost(entry) == 0.04271
    assert calls == []


def test_compute_cost_reprices_a_zero_without_provider_reported(monkeypatch):
    calls = _price_from_catalogue_at(monkeypatch, 0.42)
    for metadata in ({}, {"cost_source": "catalogue"}, {"cost_source": "unpriced"}):
        entry = {
            "cost": 0,
            "metadata": metadata,
            "model": "perplexity/sonar",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }

        assert _compute_cost(entry) == 0.42, metadata
    assert len(calls) == 3


def test_compute_cost_reprices_an_unusable_provider_reported_cost(monkeypatch):
    _price_from_catalogue_at(monkeypatch, 0.42)
    for cost in (-1, None, "0", True, float("nan")):
        entry = {
            "cost": cost,
            "metadata": {"cost_source": "provider_reported"},
            "model": "perplexity/sonar",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }

        assert _compute_cost(entry) == 0.42, cost

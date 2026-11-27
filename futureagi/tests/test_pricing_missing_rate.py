import pytest
from futureagi.pricing import get_model_rate, PriceStatus
from futureagi.api.models import calculate_cost

def test_missing_model_rate():
    model_id = "unknown-model"
    rate, status = get_model_rate(model_id)
    assert rate is None
    assert status == PriceStatus.UNAVAILABLE

    result = calculate_cost(model_id, token_count=1234)
    assert result["amount"] == 0.0
    assert result["price_status"] == PriceStatus.UNAVAILABLE.value

def test_known_model_rate():
    model_id = "cohere-embed-v2"
    rate, status = get_model_rate(model_id)
    assert rate == 0.10
    assert status == PriceStatus.KNOWN

    # 1,000,000 tokens should cost exactly the rate.
    result = calculate_cost(model_id, token_count=1_000_000)
    assert result["amount"] == pytest.approx(0.10)
    assert result["price_status"] == PriceStatus.KNOWN.value

"""call_llm_raw contract: parse the x-agentcc-cost header when present; a
missing/malformed header yields 0.0 without raising."""

from unittest.mock import MagicMock


def _raw(headers):
    raw = MagicMock()
    raw.headers = headers
    raw.parse.return_value = MagicMock(name="parsed_response")
    return raw


def test_captures_cost_header():
    from ee.usage.services.gateway_llm_client import call_llm_raw

    client = MagicMock()
    raw = _raw({"x-agentcc-cost": "0.0123"})
    client.chat.completions.with_raw_response.create.return_value = raw

    result = call_llm_raw(client, model="m", messages=[])

    assert result.response is raw.parse.return_value
    assert result.cost_usd == 0.0123


def test_malformed_cost_header_is_zero():
    from ee.usage.services.gateway_llm_client import call_llm_raw

    client = MagicMock()
    client.chat.completions.with_raw_response.create.return_value = _raw(
        {"x-agentcc-cost": "not-a-float"}
    )
    # Malformed header is swallowed (never raises) and bills nothing.
    assert call_llm_raw(client, model="m", messages=[]).cost_usd == 0.0


def test_missing_header_is_zero():
    from ee.usage.services.gateway_llm_client import call_llm_raw

    client = MagicMock()
    client.chat.completions.with_raw_response.create.return_value = _raw(None)
    assert call_llm_raw(client, model="m", messages=[]).cost_usd == 0.0

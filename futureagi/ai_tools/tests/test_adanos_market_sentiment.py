"""Tests for the optional Adanos market sentiment tool."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import ValidationError

from ai_tools.registry import registry
from ai_tools.tools.web.adanos_market_sentiment import (
    ADANOS_CONNECT_TIMEOUT_SECONDS,
    ADANOS_READ_TIMEOUT_SECONDS,
    MAX_CONTENT_CHARS,
    AdanosMarketSentimentInput,
    AdanosMarketSentimentTool,
)

REQUESTS_GET = "ai_tools.tools.web.adanos_market_sentiment.requests.get"


def _response(payload: object) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_tool_is_registered() -> None:
    tool = registry.get("get_market_sentiment")

    assert isinstance(tool, AdanosMarketSentimentTool)
    assert tool.category == "web"


def test_input_schema_describes_supported_operations() -> None:
    schema = AdanosMarketSentimentTool().input_schema

    assert schema["properties"]["operation"]["enum"] == [
        "asset",
        "trending",
        "market",
    ]
    assert schema["properties"]["limit"]["maximum"] == 20


def test_missing_api_key_returns_configuration_error() -> None:
    params = AdanosMarketSentimentInput(symbol="AAPL")

    with patch.dict("os.environ", {}, clear=True):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert result.is_error
    assert result.error_code == "CONFIGURATION_ERROR"
    assert "ADANOS_API_KEY" in result.content


@patch(REQUESTS_GET)
def test_fetches_stock_sentiment(mock_get: MagicMock) -> None:
    mock_get.return_value = _response({"ticker": "TSLA", "sentiment": 0.64})
    params = AdanosMarketSentimentInput(
        operation="asset",
        asset_type="stock",
        source="x",
        symbol=" $tsla ",
        from_date=date(2026, 7, 1),
        to_date=date(2026, 7, 20),
    )

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert not result.is_error
    assert result.data["provider"] == "adanos"
    assert result.data["request"]["symbol"] == "TSLA"
    assert result.data["response"]["ticker"] == "TSLA"
    mock_get.assert_called_once_with(
        "https://api.adanos.org/x/stocks/v1/stock/TSLA",
        headers={"Accept": "application/json", "X-API-Key": "placeholder"},
        params={"from": "2026-07-01", "to": "2026-07-20"},
        timeout=(ADANOS_CONNECT_TIMEOUT_SECONDS, ADANOS_READ_TIMEOUT_SECONDS),
    )


@patch(REQUESTS_GET)
def test_fetches_crypto_trending_from_reddit(mock_get: MagicMock) -> None:
    mock_get.return_value = _response([{"symbol": "BTC"}])
    params = AdanosMarketSentimentInput(
        operation="trending",
        asset_type="crypto",
        source="reddit",
        limit=5,
    )

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert not result.is_error
    assert result.data["response"] == [{"symbol": "BTC"}]
    assert mock_get.call_args.args[0] == (
        "https://api.adanos.org/reddit/crypto/v1/trending"
    )
    assert mock_get.call_args.kwargs["params"] == {"limit": 5}


@patch(REQUESTS_GET)
def test_market_operation_omits_limit(mock_get: MagicMock) -> None:
    mock_get.return_value = _response({"sentiment": "bullish"})
    params = AdanosMarketSentimentInput(
        operation="market",
        source="news",
        limit=20,
    )

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        AdanosMarketSentimentTool().execute(params, MagicMock())

    assert mock_get.call_args.args[0] == (
        "https://api.adanos.org/news/stocks/v1/market-sentiment"
    )
    assert mock_get.call_args.kwargs["params"] == {}


@pytest.mark.parametrize(
    "values",
    [
        {"operation": "asset", "asset_type": "stock", "symbol": "AAPL/../admin"},
        {"operation": "asset", "asset_type": "stock", "symbol": "1"},
        {"operation": "asset", "asset_type": "crypto", "symbol": "BTC ETH"},
        {"operation": "trending", "asset_type": "crypto", "source": "news"},
        {
            "operation": "market",
            "from_date": "2026-07-20",
            "to_date": "2026-07-01",
        },
        {"operation": "market", "from_date": "20260701"},
    ],
)
def test_rejects_invalid_requests(values: dict) -> None:
    with pytest.raises(ValidationError):
        AdanosMarketSentimentInput.model_validate(values)


@patch(REQUESTS_GET)
def test_timeout_returns_stable_error(mock_get: MagicMock) -> None:
    mock_get.side_effect = requests.exceptions.ReadTimeout("network detail")
    params = AdanosMarketSentimentInput(symbol="AAPL")

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert result.is_error
    assert result.error_code == "TIMEOUT_ERROR"
    assert "network detail" not in result.content


@patch(REQUESTS_GET)
def test_http_error_does_not_expose_response_body(mock_get: MagicMock) -> None:
    response = requests.Response()
    response.status_code = 401
    response._content = b"sensitive upstream detail"
    error = requests.exceptions.HTTPError(response=response)
    mock_get.return_value = MagicMock(spec=requests.Response)
    mock_get.return_value.raise_for_status.side_effect = error
    params = AdanosMarketSentimentInput(symbol="AAPL")

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert result.is_error
    assert result.error_code == "EXTERNAL_SERVICE_ERROR"
    assert "HTTP 401" in result.content
    assert "sensitive upstream detail" not in result.content


@patch(REQUESTS_GET)
def test_invalid_json_returns_external_service_error(mock_get: MagicMock) -> None:
    response = _response(None)
    response.json.side_effect = ValueError("invalid response detail")
    mock_get.return_value = response
    params = AdanosMarketSentimentInput(symbol="AAPL")

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert result.is_error
    assert result.error_code == "EXTERNAL_SERVICE_ERROR"
    assert "invalid response detail" not in result.content


@patch(REQUESTS_GET)
def test_network_error_returns_external_service_error(mock_get: MagicMock) -> None:
    mock_get.side_effect = requests.exceptions.ConnectionError("network detail")
    params = AdanosMarketSentimentInput(symbol="AAPL")

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert result.is_error
    assert result.error_code == "EXTERNAL_SERVICE_ERROR"
    assert "network detail" not in result.content


@patch(REQUESTS_GET)
def test_large_response_is_truncated_only_in_content(mock_get: MagicMock) -> None:
    payload = {"items": [{"text": "x" * MAX_CONTENT_CHARS}]}
    mock_get.return_value = _response(payload)
    params = AdanosMarketSentimentInput(symbol="AAPL")

    with patch.dict("os.environ", {"ADANOS_API_KEY": "placeholder"}):
        result = AdanosMarketSentimentTool().execute(params, MagicMock())

    assert "[truncated; full response is available in structured data]" in (
        result.content
    )
    assert result.data["response"] == payload

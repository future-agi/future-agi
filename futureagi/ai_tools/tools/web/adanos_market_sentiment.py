"""Adanos market sentiment tool for AI agents."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Literal

import requests
import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, field_validator, model_validator

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)

ADANOS_API_BASE_URL = "https://api.adanos.org"
ADANOS_CONNECT_TIMEOUT_SECONDS = 10
ADANOS_READ_TIMEOUT_SECONDS = 95
MAX_CONTENT_CHARS = 12_000

_OPERATION_PATHS = {
    "asset": "asset",
    "trending": "trending",
    "market": "market-sentiment",
}
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STOCK_SYMBOL_PATTERN = re.compile(r"^(?:[A-Z0-9]{1,10}|[A-Z0-9]{1,8}[.-][A-Z])$")
_CRYPTO_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,20}$")


def _normalize_symbol(symbol: str | None) -> str:
    return (symbol or "").strip().upper().removeprefix("$")


class AdanosMarketSentimentInput(PydanticBaseModel):
    operation: Literal["asset", "trending", "market"] = Field(
        default="asset",
        description=(
            "Data to retrieve: one asset, trending assets, or aggregate market sentiment"
        ),
    )
    asset_type: Literal["stock", "crypto"] = Field(
        default="stock",
        description="Asset class to query",
    )
    source: Literal["reddit", "x", "news", "polymarket"] = Field(
        default="reddit",
        description=(
            "Sentiment source for stocks. Crypto sentiment currently supports Reddit only"
        ),
    )
    symbol: str | None = Field(
        default=None,
        description="Stock ticker or crypto symbol. Required for the asset operation",
    )
    from_date: date | None = Field(
        default=None,
        description="Optional inclusive UTC start date in YYYY-MM-DD format",
    )
    to_date: date | None = Field(
        default=None,
        description="Optional inclusive UTC end date in YYYY-MM-DD format",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum results for the trending operation (default 10, max 20)",
    )

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def validate_date_format(cls, value: object) -> object:
        if isinstance(value, str) and not _DATE_PATTERN.fullmatch(value):
            raise ValueError("Dates must use YYYY-MM-DD format")
        return value

    @model_validator(mode="after")
    def validate_request(self) -> AdanosMarketSentimentInput:
        if self.asset_type == "crypto" and self.source != "reddit":
            raise ValueError(
                "Crypto sentiment currently supports the Reddit source only"
            )
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date cannot be after to_date")
        if self.operation != "asset":
            return self

        symbol = _normalize_symbol(self.symbol)
        pattern = (
            _CRYPTO_SYMBOL_PATTERN
            if self.asset_type == "crypto"
            else _STOCK_SYMBOL_PATTERN
        )
        is_valid = bool(pattern.fullmatch(symbol))
        if self.asset_type == "stock" and symbol.isdigit():
            is_valid = is_valid and len(symbol) >= 3
        if not is_valid:
            raise ValueError("A valid ticker or crypto symbol is required")
        return self


def _request_parts(params: AdanosMarketSentimentInput) -> tuple[str, dict]:
    if params.asset_type == "crypto":
        prefix = "/reddit/crypto/v1"
        asset_segment = "token"
    else:
        prefix = f"/{params.source}/stocks/v1"
        asset_segment = "stock"

    query: dict[str, str | int] = {}
    if params.from_date:
        query["from"] = params.from_date.isoformat()
    if params.to_date:
        query["to"] = params.to_date.isoformat()

    operation = _OPERATION_PATHS[params.operation]
    if operation == "asset":
        symbol = _normalize_symbol(params.symbol)
        path = f"{prefix}/{asset_segment}/{symbol}"
    else:
        path = f"{prefix}/{operation}"
        if operation == "trending":
            query["limit"] = params.limit

    return path, query


def _render_content(params: AdanosMarketSentimentInput, payload: object) -> str:
    heading = {
        "asset": "Asset Sentiment",
        "trending": "Trending Assets",
        "market": "Market Sentiment",
    }[params.operation]
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if len(rendered) > MAX_CONTENT_CHARS:
        rendered = (
            rendered[:MAX_CONTENT_CHARS].rstrip()
            + "\n... [truncated; full response is available in structured data]"
        )
    return f"## Adanos {heading}\n\n```json\n{rendered}\n```"


@register_tool
class AdanosMarketSentimentTool(BaseTool):
    name = "get_market_sentiment"
    description = (
        "Retrieve current or historical market sentiment from Adanos. Query an "
        "individual stock or crypto asset, discover trending assets, or inspect "
        "aggregate market sentiment. Stock data can use Reddit, X / FinTwit, "
        "financial news, or Polymarket; crypto data currently uses Reddit."
    )
    category = "web"
    input_model = AdanosMarketSentimentInput

    def execute(
        self,
        params: AdanosMarketSentimentInput,
        context: ToolContext,
    ) -> ToolResult:
        api_key = os.getenv("ADANOS_API_KEY", "").strip()
        if not api_key:
            return ToolResult.error(
                "Adanos API key not configured. Set ADANOS_API_KEY environment variable.",
                error_code="CONFIGURATION_ERROR",
            )

        path, query = _request_parts(params)
        try:
            response = requests.get(
                f"{ADANOS_API_BASE_URL}{path}",
                headers={"Accept": "application/json", "X-API-Key": api_key},
                params=query,
                timeout=(
                    ADANOS_CONNECT_TIMEOUT_SECONDS,
                    ADANOS_READ_TIMEOUT_SECONDS,
                ),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.Timeout:
            return ToolResult.error(
                "Adanos API request timed out. Try a shorter date range.",
                error_code="TIMEOUT_ERROR",
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            logger.warning("adanos_api_error", status=status, path=path)
            return ToolResult.error(
                f"Adanos API request failed (HTTP {status}).",
                error_code="EXTERNAL_SERVICE_ERROR",
            )
        except (requests.exceptions.RequestException, ValueError) as exc:
            logger.warning("adanos_request_failed", error_type=type(exc).__name__)
            return ToolResult.error(
                "Could not retrieve market sentiment from Adanos.",
                error_code="EXTERNAL_SERVICE_ERROR",
            )

        request_metadata = {
            "operation": params.operation,
            "asset_type": params.asset_type,
            "source": params.source,
        }
        if params.operation == "asset":
            request_metadata["symbol"] = _normalize_symbol(params.symbol)

        return ToolResult(
            content=_render_content(params, payload),
            data={
                "provider": "adanos",
                "request": request_metadata,
                "response": payload,
            },
        )

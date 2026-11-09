"""Blocked catalogue prices stay absent through both cold and cached API reads."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from agentcc.views.managed_models import CATALOG_CACHE_KEY

PRICE_FIELDS = (
    "prices",
    "price_tiers",
    "input_per_token",
    "output_per_token",
    "cache_read_per_token",
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "billed_input_per_mtok",
    "billed_output_per_mtok",
)


def _gateway_catalog():
    # Cloud red/green gates supply the actual Go handler response. Ordinary
    # Django runs use the same omitted-field wire contract without a Go build.
    if path := os.environ.get("CATALOG_PRICE_CONTRACT_OUTPUT"):
        return json.loads(Path(path).read_text())
    return {
        "markup_percent": 5,
        "providers": [],
        "models": [
            {
                "id": "acme/blocked",
                "provider": "mock",
                "vendor": "acme",
                "state": "blocked",
                "state_reason": "Not available on Future AGI credits",
            },
            {
                "id": "acme/available",
                "provider": "mock",
                "vendor": "acme",
                "state": "active",
                "prices": {"prompt": 0.000002, "completion": 0.00001},
                "input_per_token": 0.000002,
                "output_per_token": 0.00001,
                "input_per_mtok": 2,
                "output_per_mtok": 10,
                "billed_input_per_mtok": 2.1,
                "billed_output_per_mtok": 10.5,
            },
        ],
    }


@pytest.mark.integration
@pytest.mark.api
def test_catalog_blocked_prices_pass_through_and_cache(auth_client):
    catalog = _gateway_catalog()
    blocked = [m for m in catalog["models"] if m.get("state") == "blocked"]
    available = [m for m in catalog["models"] if m.get("state") != "blocked"]
    assert blocked and available
    client = MagicMock()
    client.get_catalog.return_value = catalog
    cache.delete(CATALOG_CACHE_KEY)
    try:
        with patch(
            "agentcc.views.managed_models.get_gateway_client", return_value=client
        ):
            for _ in range(2):
                response = auth_client.get("/agentcc/managed-models/catalog/")
                assert response.status_code == 200, response.json()
                result = response.json()["result"]
                assert result == catalog  # Preserve exact available prices and markup.
                for model in result["models"]:
                    if model.get("state") == "blocked":
                        for field in PRICE_FIELDS:
                            assert model.get(field) is None, (
                                model["id"],
                                field,
                                model[field],
                            )
        client.get_catalog.assert_called_once()
    finally:
        cache.delete(CATALOG_CACHE_KEY)


def test_published_catalog_contract_allows_omitted_prices():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
        ).read_text()
    )["definitions"]["ManagedCatalogModel"]
    assert not set(PRICE_FIELDS).intersection(schema.get("required", []))

"""c2.6 unions: typed absence, retry recovery and shared-client compatibility.

Only HTTP I/O is mocked; the client, views and response envelope stay real.
These are API controls, not proof that a real CLOSED org emits this refusal.
"""

from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache

from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.views.managed_models import CATALOG_CACHE_KEY

CLIENT = "agentcc.views.managed_models.get_gateway_client"
HTTP = "agentcc.services.gateway_client.httpx.Client"
STATUS = "/agentcc/managed-models/"
CATALOG = "/agentcc/managed-models/catalog/"
PRIVATE = "synthetic-private-admin-detail"


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    cache.delete(CATALOG_CACHE_KEY)
    yield
    cache.delete(CATALOG_CACHE_KEY)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("endpoint", [STATUS, CATALOG])
@pytest.mark.parametrize(
    "code", ["managed_billing_unavailable", "managed_activation_required"]
)
def test_failed_read_then_retry_recovers(auth_client, endpoint, code):
    success = (
        {"balance_usd": 12.5, "markup_percent": 5}
        if endpoint == STATUS
        else {"markup_percent": 5, "providers": [], "models": []}
    )
    client = GatewayClient("http://gateway.invalid", "synthetic-admin")
    with patch(CLIENT, return_value=client), patch(HTTP) as http:
        request = http.return_value.__enter__.return_value.request
        request.side_effect = [
            httpx.Response(
                503,
                json={"error": {"code": code, "message": PRIVATE}},
                headers={"Retry-After": "5"},
            ),
            httpx.Response(200, json=success),
        ]
        failed = auth_client.get(endpoint)
        assert failed.status_code == 503
        assert failed.json()["status"] is False
        assert failed.json()["code"] == code
        assert failed["Retry-After"] == "5"
        assert cache.get(CATALOG_CACHE_KEY) is None
        recovered = auth_client.get(endpoint)
        assert recovered.status_code == 200
        assert recovered.json()["status"] is True
        if endpoint == STATUS:
            assert recovered.json()["result"]["available"] is True
            assert recovered.json()["result"]["balance_usd"] == 12.5
        else:
            assert recovered.json()["result"] == success
            cached = auth_client.get(endpoint)
            assert cached.json() == recovered.json()
        assert request.call_count == 2


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("upstream_status", [400, 503])
def test_disabled_code_without_404_is_not_catalog_absence(auth_client, upstream_status):
    client = GatewayClient("http://gateway.invalid", "synthetic-admin")
    with patch(CLIENT, return_value=client), patch(HTTP) as http:
        http.return_value.__enter__.return_value.request.return_value = httpx.Response(
            upstream_status, json={"error": {"code": "managed_models_disabled"}}
        )
        response = auth_client.get(CATALOG)
    assert response.status_code == (503 if upstream_status == 503 else 502)
    assert response.json()["status"] is False
    assert cache.get(CATALOG_CACHE_KEY) is None


@pytest.mark.integration
@pytest.mark.api
def test_disabled_catalog_is_typed_uncached_and_recovers(auth_client):
    client = GatewayClient("http://gateway.invalid", "synthetic-admin")
    catalog = {"markup_percent": 5, "providers": [], "models": []}
    with patch(CLIENT, return_value=client), patch(HTTP) as http:
        request = http.return_value.__enter__.return_value.request
        request.side_effect = [
            httpx.Response(404, json={"error": {"code": "managed_models_disabled"}}),
            httpx.Response(200, json=catalog),
        ]
        disabled = auth_client.get(CATALOG)
        assert disabled.status_code == 200
        assert disabled.json() == {
            "status": True,
            "result": {
                "available": False,
                "markup_percent": None,
                "providers": [],
                "models": [],
            },
        }
        assert cache.get(CATALOG_CACHE_KEY) is None
        assert auth_client.get(CATALOG).json()["result"] == catalog
        assert request.call_count == 2


@pytest.mark.parametrize(
    "code", ["org_not_found", "not_found", "managed_activation_required"]
)
def test_internal_discriminator_is_not_replaced_by_public_code(code):
    with patch(HTTP) as http:
        http.return_value.__enter__.return_value.request.return_value = httpx.Response(
            404, json={"error": {"code": code, "message": PRIVATE}}
        )
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://gateway.invalid")._request("GET", "/-/mcp/status")
    error = caught.value
    assert error.error_code == code
    assert getattr(error, "code", None) == (
        code if code == "managed_activation_required" else "gateway_unavailable"
    )
    assert PRIVATE not in str(error)


def test_positional_error_code_remains_compatible():
    error = GatewayClientError("synthetic failure", 404, "org_not_found")
    assert error.status_code == 404
    assert error.error_code == "org_not_found"
    assert getattr(error, "code", None) == "gateway_unavailable"


@pytest.mark.integration
@pytest.mark.api
def test_non_json_credits_body_is_an_operational_failure(auth_client):
    client = GatewayClient("http://gateway.invalid", "synthetic-admin")
    with patch(CLIENT, return_value=client), patch(HTTP) as http:
        http.return_value.__enter__.return_value.request.return_value = httpx.Response(
            200, text=PRIVATE
        )
        response = auth_client.get(STATUS)
    assert response.status_code == 502
    assert response.json()["status"] is False
    assert response.json()["code"] == "gateway_unavailable"
    assert PRIVATE not in response.content.decode()

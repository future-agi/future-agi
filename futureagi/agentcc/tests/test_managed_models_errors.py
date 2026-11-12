"""Managed-model read errors are recoverable failures, not missing features.

All gateway I/O is mocked. Transport-through-view cases bind the public error
contract without calling a gateway, a provider, or a billing service.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.cache import cache

from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.views.managed_models import CATALOG_CACHE_KEY

CLIENT = "agentcc.views.managed_models.get_gateway_client"
HTTP = "agentcc.services.gateway_client.httpx.Client"
STATUS = "/agentcc/managed-models/"
CATALOG = "/agentcc/managed-models/catalog/"
PRIVATE = "http://internal-gateway.invalid/ Traceback fake-admin-secret"
BILLING_MESSAGE = "Managed billing is temporarily unavailable. Please retry."
UNAVAILABLE_MESSAGE = "The model service is temporarily unavailable. Please retry."
ACTIVATION_MESSAGE = (
    "Managed models are temporarily unavailable for this organization. "
    "Please retry later or contact your administrator."
)


def _activation_payload():
    # A-PRIME 8b0f2d9cfb: models/managed_activation.go + errors{,_billing}.go.
    # This is the producer wire fixture, not a claim that catalog emits it.
    return {
        "error": {
            "message": (
                "Managed models are temporarily unavailable for this organization. "
                "Retry later or contact your administrator; your own provider keys "
                "still work."
            ),
            "type": "server_error",
            "param": None,
            "code": "managed_activation_required",
            "metadata": {
                "limit_source": "agentcc_spend_guard",
                "scope": "org",
                "remedy_hint": "retry",
                "resets_at": None,
            },
        }
    }


def _payload(code, **metadata):
    return {
        "error": {
            "code": code,
            "message": PRIVATE,
            "type": PRIVATE,
            "param": PRIVATE,
            "metadata": metadata,
        },
        "debug": PRIVATE,
    }


def _gateway_error(status, payload, headers=None):
    """Produce the real client exception using a mocked HTTP response."""
    with patch(HTTP) as http:
        http.return_value.__enter__.return_value.request.return_value = httpx.Response(
            status, json=payload, headers=headers
        )
        with pytest.raises(GatewayClientError) as raised:
            GatewayClient("http://gateway.invalid", "fake-token").get_catalog()
    return raised.value


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    cache.delete(CATALOG_CACHE_KEY)
    yield
    cache.delete(CATALOG_CACHE_KEY)


def _assert_failure(response, status, code, message):
    assert response.status_code == status
    body = response.json()
    assert body["status"] is False
    assert body["code"] == code
    assert body["message"] == message
    assert body["detail"] == message
    assert body["error"] == message
    assert body["result"] == message
    assert "available" not in body
    assert "internal-gateway" not in response.content.decode()
    assert "fake-admin-secret" not in response.content.decode()
    assert "Traceback" not in response.content.decode()
    return body


@pytest.mark.integration
@pytest.mark.api
class TestManagedModelsErrorEndpoints:
    @pytest.mark.parametrize("endpoint", [STATUS, CATALOG])
    @pytest.mark.parametrize("upstream_status", [400, 404, 503])
    @pytest.mark.parametrize("tainted", [False, True])
    def test_activation_refusal_stays_typed_and_operational(
        self, auth_client, endpoint, upstream_status, tainted
    ):
        payload = _activation_payload()
        if tainted:
            payload["debug"] = PRIVATE
            payload["error"].update(message=PRIVATE, type=PRIVATE, param=PRIVATE)
            payload["error"]["metadata"].update(
                reason="redis_unavailable", org_id=PRIVATE, legacy_state=PRIVATE
            )
        client = GatewayClient("http://gateway.invalid", "fake-token")
        with patch(CLIENT, return_value=client), patch(HTTP) as http:
            request = http.return_value.__enter__.return_value.request
            request.return_value = httpx.Response(
                upstream_status,
                json=payload,
                headers={
                    "rEtRy-AfTeR": "5",
                    "X-Upstream-Host": PRIVATE,
                    "Authorization": PRIVATE,
                    "Set-Cookie": PRIVATE,
                },
            )
            response = auth_client.get(endpoint)
        request.assert_called_once()
        body = _assert_failure(
            response,
            503 if upstream_status == 503 else 502,
            "managed_activation_required",
            ACTIVATION_MESSAGE,
        )
        assert body["type"] == (
            "service_unavailable" if upstream_status == 503 else "server_error"
        )
        # Activation metadata is not part of the existing billing-only allowlist.
        assert body["metadata"] == {}
        assert set(body) == {
            "status",
            "type",
            "code",
            "message",
            "detail",
            "error",
            "result",
            "metadata",
        }
        assert response["Retry-After"] == "5"
        for header in ("X-Upstream-Host", "Authorization", "Set-Cookie"):
            assert header not in response
        assert cache.get(CATALOG_CACHE_KEY) is None

    def test_billing_outage_is_not_a_successful_absence(self, auth_client):
        client = MagicMock()
        client.get_org_credits.side_effect = _gateway_error(
            503,
            _payload(
                "managed_billing_unavailable",
                reason="redis_unavailable",
                remedy_hint="retry",
                debug=PRIVATE,
            ),
        )
        with patch(CLIENT, return_value=client):
            response = auth_client.get(STATUS)

        body = _assert_failure(
            response, 503, "managed_billing_unavailable", BILLING_MESSAGE
        )
        assert body["type"] == "service_unavailable"
        assert body["metadata"] == {
            "reason": "redis_unavailable",
            "remedy_hint": "retry",
        }

    def test_only_explicit_disabled_is_successful_absence(self, auth_client):
        client = MagicMock()
        client.get_org_credits.side_effect = _gateway_error(
            404, _payload("managed_models_disabled")
        )
        with patch(CLIENT, return_value=client):
            response = auth_client.get(STATUS)

        assert response.status_code == 200
        assert response.json() == {
            "status": True,
            "result": {
                "available": False,
                "enabled": False,
                "balance_usd": None,
                "markup_percent": None,
            },
        }

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502, 503, 504])
    @pytest.mark.parametrize(
        "endpoint, method", [(STATUS, "get_org_credits"), (CATALOG, "get_catalog")]
    )
    def test_unknown_upstream_failure_is_operational(
        self, auth_client, status, endpoint, method
    ):
        client = MagicMock()
        getattr(client, method).side_effect = _gateway_error(
            status, _payload(PRIVATE, reason=PRIVATE)
        )
        with patch(CLIENT, return_value=client):
            response = auth_client.get(endpoint)

        body = _assert_failure(
            response,
            503 if status == 503 else 502,
            "gateway_unavailable",
            UNAVAILABLE_MESSAGE,
        )
        assert body["metadata"] == {}

    def test_disabled_code_without_404_is_not_absence(self, auth_client):
        client = MagicMock()
        client.get_org_credits.side_effect = _gateway_error(
            503, _payload("managed_models_disabled")
        )
        with patch(CLIENT, return_value=client):
            response = auth_client.get(STATUS)
        assert response.status_code == 503
        assert response.json()["status"] is False

    def test_catalog_keeps_typed_error_and_safe_retry_header(self, auth_client):
        client = MagicMock()
        client.get_catalog.side_effect = _gateway_error(
            503,
            _payload(
                "managed_billing_unavailable",
                reason="account_state_missing",
                remedy_hint="retry",
                stack=PRIVATE,
                authorization=PRIVATE,
            ),
            {"Retry-After": "30", "X-Upstream-Host": PRIVATE, "Set-Cookie": PRIVATE},
        )
        with patch(CLIENT, return_value=client):
            first = auth_client.get(CATALOG)
            second = auth_client.get(CATALOG)

        for response in (first, second):
            body = _assert_failure(
                response, 503, "managed_billing_unavailable", BILLING_MESSAGE
            )
            assert body["type"] == "service_unavailable"
            assert body["metadata"] == {
                "reason": "account_state_missing",
                "remedy_hint": "retry",
            }
            assert response["Retry-After"] == "30"
            assert "X-Upstream-Host" not in response
            assert "Set-Cookie" not in response
        assert client.get_catalog.call_count == 2
        assert cache.get(CATALOG_CACHE_KEY) is None

    @pytest.mark.parametrize("endpoint", [STATUS, CATALOG])
    @pytest.mark.parametrize(
        "failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
    )
    def test_transport_failure_is_sanitized(self, auth_client, endpoint, failure):
        client = GatewayClient("http://gateway.invalid", "fake-token")
        with patch(CLIENT, return_value=client), patch(HTTP) as http:
            http.return_value.__enter__.return_value.request.side_effect = failure(
                PRIVATE
            )
            response = auth_client.get(endpoint)
        _assert_failure(response, 503, "gateway_unavailable", UNAVAILABLE_MESSAGE)

    @pytest.mark.parametrize("endpoint", [STATUS, CATALOG])
    @pytest.mark.parametrize("upstream_status", [404, 503])
    def test_real_transport_through_view(self, auth_client, endpoint, upstream_status):
        disabled = upstream_status == 404
        code = "managed_models_disabled" if disabled else "managed_billing_unavailable"
        client = GatewayClient("http://gateway.invalid", "fake-token")
        with patch(CLIENT, return_value=client), patch(HTTP) as http:
            request = http.return_value.__enter__.return_value.request
            request.return_value = httpx.Response(
                upstream_status,
                json=_payload(code, reason="redis_unavailable", remedy_hint="retry"),
                headers={"Retry-After": "2"},
            )
            response = auth_client.get(endpoint)
        request.assert_called_once()
        # c2.6 deliberately reports typed absence successfully on both reads.
        if disabled:
            assert response.status_code == 200
            assert response.json()["result"]["available"] is False
        else:
            _assert_failure(response, upstream_status, code, BILLING_MESSAGE)
            assert response["Retry-After"] == "2"

    @pytest.mark.parametrize("upstream_status", [200, 502, 503])
    def test_non_json_upstream_body_is_sanitized(self, auth_client, upstream_status):
        client = GatewayClient("http://gateway.invalid", "fake-token")
        with patch(CLIENT, return_value=client), patch(HTTP) as http:
            http.return_value.__enter__.return_value.request.return_value = (
                httpx.Response(upstream_status, text=PRIVATE)
            )
            response = auth_client.get(CATALOG)
        _assert_failure(
            response,
            503 if upstream_status == 503 else 502,
            "gateway_unavailable",
            UNAVAILABLE_MESSAGE,
        )


class TestGatewayManagedErrorSanitization:
    @pytest.mark.parametrize(
        "retry_after",
        [
            None,
            "",
            "-1",
            "1.5",
            "99999999999",
            "١",
            PRIVATE,
            "Wed, 21 Oct 2026 07:28:00 GMT",
        ],
    )
    def test_activation_refusal_does_not_relax_retry_header_allowlist(
        self, retry_after
    ):
        error = GatewayClientError(
            "Gateway returned 503.",
            503,
            payload=_activation_payload(),
            headers={"Retry-After": retry_after, "Authorization": PRIVATE},
        )
        assert error.status_code == 503
        assert error.code == "managed_activation_required"
        assert error.public_message == ACTIVATION_MESSAGE
        assert error.metadata == {}
        assert error.headers == {}

    @pytest.mark.parametrize("reason", ["redis_unavailable", "account_state_missing"])
    def test_client_keeps_only_safe_structured_fields(self, reason):
        error = _gateway_error(
            503,
            _payload(
                "managed_billing_unavailable",
                reason=reason,
                remedy_hint="retry",
                debug=PRIVATE,
            ),
            {"rEtRy-AfTeR": "5", "Authorization": PRIVATE, "X-Request-ID": PRIVATE},
        )
        assert error.status_code == 503
        assert error.code == "managed_billing_unavailable"
        assert error.public_message == BILLING_MESSAGE
        assert error.metadata == {"reason": reason, "remedy_hint": "retry"}
        assert error.headers == {"Retry-After": "5"}
        assert PRIVATE not in str(error)

    @pytest.mark.parametrize("payload", [None, [], {"error": PRIVATE}, {"error": []}])
    def test_unstructured_error_is_not_echoed(self, payload):
        error = _gateway_error(502, payload)
        assert error.code == "gateway_unavailable"
        assert error.public_message == UNAVAILABLE_MESSAGE
        assert error.metadata == {}
        assert error.headers == {}
        assert PRIVATE not in str(error)

    @pytest.mark.parametrize(
        "value", [PRIVATE, ["redis_unavailable"], {"reason": PRIVATE}, None]
    )
    def test_metadata_values_are_not_arbitrary_strings_or_objects(self, value):
        error = _gateway_error(
            503,
            _payload("managed_billing_unavailable", reason=value, remedy_hint=value),
        )
        assert error.metadata == {}

    @pytest.mark.parametrize(
        "value", [PRIVATE, "-1", "1.5", "1e2", "999999999999999999999", "١"]
    )
    def test_retry_header_requires_bounded_ascii_seconds(self, value):
        # Constructor input also exercises non-ASCII values HTTPX cannot encode.
        error = GatewayClientError("failure", 503, headers={"Retry-After": value})
        assert error.headers == {}

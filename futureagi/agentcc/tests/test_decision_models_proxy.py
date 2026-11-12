"""The evaluator picker has a scoped live listing."""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.services.gateway_client import GatewayClient

pytestmark = pytest.mark.unit

PATH = "/agentcc/decisions/models/"
LISTING = {
    "object": "list",
    "data": [
        {
            "id": "typesafe/jev-1.13.0",
            "object": "model",
            "provider": "typesafe",
            "format": "systemone",
            "pricing": {"input_per_mtok": 0.042, "output_per_mtok": 0},
        },
        {
            "id": "perplexity/pplx-decider-v1.1-27b",
            "object": "model",
            "provider": "perplexity",
            "format": "systemone",
            "pricing": {"input_per_mtok": 0.02, "output_per_mtok": 0},
        },
    ],
}


def _request(*, authenticated=True):
    request = APIRequestFactory().get(PATH, {"org_id": "other-org"})
    request.organization = SimpleNamespace(id="current-org")
    request.workspace = SimpleNamespace(
        id="current-workspace", organization_id="current-org"
    )
    if authenticated:
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    return request


def _dispatch(request):
    # Resolve the actual mounted URL: a view-only test would miss the regression.
    match = resolve(PATH)
    return match.func(request, *match.args, **match.kwargs)


@pytest.fixture
def transport():
    client = GatewayClient("https://gateway.example.test", "synthetic-admin")
    with (
        patch(
            "agentcc.services.gateway_client.get_gateway_client", return_value=client
        ),
        patch("agentcc.services.gateway_client.httpx.Client") as http,
    ):
        send = http.return_value.__enter__.return_value.request
        send.return_value = httpx.Response(200, json=LISTING)
        yield send


def test_picker_route_returns_canonical_models_in_result_envelope(transport):
    response = _dispatch(_request())

    assert response.status_code == 200
    assert response.data["result"] == LISTING
    assert response["Cache-Control"] == "no-store"
    transport.assert_called_once_with(
        "GET",
        "https://gateway.example.test/-/orgs/current-org/decisions/models",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer synthetic-admin",
        },
        params=None,
        json=None,
    )


@pytest.mark.parametrize(
    "missing", ["organization", "workspace", "mismatched_workspace"]
)
def test_picker_requires_matching_workspace_context(transport, missing):
    request = _request()
    if missing == "mismatched_workspace":
        request.workspace.organization_id = "other-org"
    else:
        setattr(request, missing, None)

    assert _dispatch(request).status_code == 400
    transport.assert_not_called()


def test_picker_requires_session_authentication(transport):
    response = _dispatch(_request(authenticated=False))

    assert response.status_code in (401, 403)
    transport.assert_not_called()


def test_unconfigured_org_returns_a_successful_empty_listing(transport):
    transport.return_value = httpx.Response(200, json={"object": "list", "data": []})

    response = _dispatch(_request())

    assert response.status_code == 200
    assert response.data["result"] == {"object": "list", "data": []}


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_gateway_failures_do_not_log_out_the_browser_or_become_empty(transport, status):
    transport.return_value = httpx.Response(
        status, json={"error": "synthetic-private-detail"}
    )

    response = _dispatch(_request())

    assert response.status_code == 502
    assert "synthetic-private-detail" not in str(response.data)
    assert response.data.get("result") != {"object": "list", "data": []}


def test_unreachable_gateway_is_retryable(transport):
    transport.side_effect = httpx.ConnectError("synthetic-private-detail")

    response = _dispatch(_request())

    assert response.status_code == 503
    assert "synthetic-private-detail" not in str(response.data)


@pytest.mark.parametrize("body", [[], {}, {"object": "list", "data": {}}, "not-json"])
def test_invalid_gateway_listing_is_not_success(transport, body):
    transport.return_value = (
        httpx.Response(200, text=body)
        if isinstance(body, str)
        else httpx.Response(200, json=body)
    )

    assert _dispatch(_request()).status_code == 502


# A gateway older than the listing (v1.58.0, or a roll that updates the backend
# first) has no /-/orgs/{org_id}/decisions/models route: its router answers 404
# with code not_found and "Unknown endpoint: <path>". Retrying cannot help, so
# the backend names it: 501 decisions_not_supported, in its own words.
PRIVATE = "synthetic-private-detail"
NOT_SUPPORTED_MESSAGE = (
    "This gateway version doesn't list decision models. "
    "Update the gateway to use them."
)


def _unknown_route(path="/-/orgs/current-org/decisions/models"):
    return {
        "error": {
            "message": "Unknown endpoint: " + path,
            "type": "not_found",
            "code": "not_found",
        }
    }


def test_a_gateway_without_the_listing_is_named_and_not_retryable(transport):
    transport.return_value = httpx.Response(404, json=_unknown_route())

    response = _dispatch(_request())

    assert response.status_code == 501
    assert response.data["code"] == "decisions_not_supported"
    assert NOT_SUPPORTED_MESSAGE in str(response.data)
    assert "Unknown endpoint" not in str(response.data)
    assert response.data.get("result") != {"object": "list", "data": []}
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "status, body",
    [
        (404, {"error": {"message": PRIVATE, "code": "org_not_found"}}),
        (404, {"error": PRIVATE}),
        (500, _unknown_route()),
        (502, {"error": {"message": PRIVATE, "code": "bad_gateway"}}),
    ],
    ids=["another 404 code", "an untyped 404", "a 500", "a 502"],
)
def test_other_gateway_failures_keep_the_retryable_answer(transport, status, body):
    transport.return_value = httpx.Response(status, json=body)

    response = _dispatch(_request())

    assert response.status_code == 502
    assert response.data["code"] != "decisions_not_supported"
    assert PRIVATE not in str(response.data)


def test_an_unavailable_gateway_keeps_the_retryable_answer(transport):
    transport.return_value = httpx.Response(
        503, json={"error": {"code": "unavailable"}}
    )

    response = _dispatch(_request())

    assert response.status_code == 503
    assert response.data["code"] != "decisions_not_supported"

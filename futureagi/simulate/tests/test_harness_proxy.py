"""
API tests for HarnessProxyView.

Tests cover:
- Auth gating (anonymous rejected)
- Response enrichment with platform run_test/execution ids
- Platform ids stripped from the forwarded body and persisted via harness_links
- Harness error/timeout passthrough (409, 502)
- Path traversal refusal
- SSE Accept header not triggering DRF content negotiation 406
- Non-POST requests to streaming paths (say/run) rejected with 405 before any upstream call
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.urls import reverse

from simulate.services import harness_links

STATUS_PAYLOAD = {"session": {"id": "abc123"}, "stage": "build", "busy": False}


@pytest.fixture(autouse=True)
def links_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_LINKS_DIR", str(tmp_path / "platform-links"))


def _url(path):
    return reverse("simulate:harness-proxy", kwargs={"path": path})


def _drain(response):
    async def collect():
        return b"".join([chunk async for chunk in response.streaming_content])

    return async_to_sync(collect)()


def test_anonymous_requests_are_rejected(api_client):
    assert api_client.get(_url("status")).status_code in (401, 403)


@patch("simulate.views.harness_proxy.httpx.request")
def test_status_is_enriched_with_platform_ids(mock_request, auth_client):
    harness_links.remember("abc123", "rt-1", "ex-1")
    mock_request.return_value = httpx.Response(
        200, json=STATUS_PAYLOAD, headers={"content-type": "application/json"}
    )
    answered = auth_client.get(_url("status")).json()
    assert answered["run_test_id"] == "rt-1"
    assert answered["execution_id"] == "ex-1"


@patch("simulate.views.harness_proxy.httpx.request")
def test_session_creation_strips_and_stores_platform_ids(mock_request, auth_client):
    mock_request.return_value = httpx.Response(
        200, json=STATUS_PAYLOAD, headers={"content-type": "application/json"}
    )
    auth_client.post(
        _url("sessions"),
        data=json.dumps({"agent": "support", "run_test_id": "rt-9", "execution_id": "ex-9"}),
        content_type="application/json",
    )
    forwarded = mock_request.call_args.kwargs["json"]
    assert "run_test_id" not in forwarded and forwarded == {"agent": "support"}
    assert harness_links.lookup("abc123") == {"run_test_id": "rt-9", "execution_id": "ex-9"}


@patch("simulate.views.harness_proxy.httpx.request")
def test_harness_errors_pass_through(mock_request, auth_client):
    mock_request.return_value = httpx.Response(
        409,
        json={"error": "still working on the last thing"},
        headers={"content-type": "application/json"},
    )
    answered = auth_client.post(
        _url("stage"), data=json.dumps({}), content_type="application/json"
    )
    assert answered.status_code == 409
    assert "run_test_id" not in answered.json()


@patch("simulate.views.harness_proxy.httpx.request")
def test_unreachable_harness_maps_to_502(mock_request, auth_client):
    mock_request.side_effect = httpx.ConnectError("boom")
    assert auth_client.get(_url("status")).status_code == 502


def test_traversal_is_refused(auth_client):
    assert auth_client.get(_url("..%2Fadmin")).status_code == 404


@patch("simulate.views.harness_proxy.httpx.request")
def test_sse_accept_header_is_not_rejected(mock_request, auth_client):
    mock_request.return_value = httpx.Response(
        200, json=STATUS_PAYLOAD, headers={"content-type": "application/json"}
    )
    answered = auth_client.get(_url("status"), HTTP_ACCEPT="text/event-stream")
    assert answered.status_code == 200


@patch("simulate.views.harness_proxy.httpx.request")
def test_non_post_to_streaming_paths_is_405(mock_request, auth_client):
    answered = auth_client.get(_url("say"))
    assert answered.status_code == 405
    mock_request.assert_not_called()


@patch("simulate.views.harness_proxy.httpx.Client")
def test_sse_stream_relays_chunks(mock_client_cls, auth_client):
    chunks = [b'data: {"kind": "text"}\n\n', b'data: {"kind": "status"}\n\n']
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {}
    upstream.iter_bytes.return_value = iter(chunks)

    stream_cm = MagicMock()
    stream_cm.__enter__.return_value = upstream
    stream_cm.__exit__.return_value = None

    mock_client = MagicMock()
    mock_client.stream.return_value = stream_cm
    mock_client_cls.return_value = mock_client

    answered = auth_client.post(
        _url("say"), data=json.dumps({}), content_type="application/json"
    )
    assert answered.status_code == 200
    assert answered["Content-Type"] == "text/event-stream"
    assert _drain(answered) == b"".join(chunks)

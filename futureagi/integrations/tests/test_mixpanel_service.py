"""Mixpanel export acknowledgements are data-level, not just HTTP-level."""

import base64
import json
from unittest.mock import patch

import pytest
import requests

from integrations.services.mixpanel_service import MixpanelService

CREDENTIALS = {"project_token": "synthetic-project-token"}
EVENTS = [
    {
        "event": "agentcc_request",
        "time": 1700000000,
        "distinct_id": "synthetic-user",
        "properties": {"request_id": f"synthetic-request-{i}", "model": "test"},
    }
    for i in range(2)
]


def response(body, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result.url = MixpanelService.IMPORT_URL
    return result


def acknowledged(count):
    return response({"code": 200, "status": "OK", "num_records_imported": count})


@pytest.fixture
def post():
    with patch("integrations.services.mixpanel_service.requests.post") as mock:
        yield mock


@pytest.mark.parametrize("body", [0, 1, {}, None, [], "1", {"status": 1}])
def test_http_success_is_not_an_import_acknowledgement(post, body):
    post.return_value = response(body)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        MixpanelService().export_events(CREDENTIALS, EVENTS)


@pytest.mark.parametrize("body", [0, 1, {}, None, {"status": 1}])
def test_track_acceptance_does_not_validate_credentials(post, body):
    post.return_value = response(body)
    result = MixpanelService().validate_credentials("", CREDENTIALS)
    assert result["valid"] is False


@pytest.mark.parametrize("secret", [None, "synthetic-project-secret"])
def test_strict_import_uses_supported_auth_and_exact_count(post, secret):
    post.return_value = acknowledged(2)
    credentials = {**CREDENTIALS}
    if secret:
        credentials["api_secret"] = secret
    original = json.dumps(EVENTS, sort_keys=True)
    assert MixpanelService().export_events(credentials, EVENTS) == {"sent": 2}
    args, kwargs = post.call_args
    assert args == (MixpanelService.IMPORT_URL,)
    assert kwargs["params"] == {"strict": "1"}
    assert kwargs["timeout"] == MixpanelService.TIMEOUT
    auth = kwargs["headers"]["Authorization"].removeprefix("Basic ")
    assert (
        base64.b64decode(auth).decode() == f"{secret or CREDENTIALS['project_token']}:"
    )
    for event in kwargs["json"]:
        assert "token" not in event["properties"]
        assert len(event["properties"]["$insert_id"]) == 36
    assert json.dumps(EVENTS, sort_keys=True) == original


@pytest.mark.parametrize("count", [0, 1, 3, -1, True, "2", 2.0, None])
def test_incomplete_or_invalid_count_never_reports_sent(post, count):
    post.return_value = acknowledged(count)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        MixpanelService().export_events(CREDENTIALS, EVENTS)


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "Bad Request"},
        {"code": 400},
        {"error": "synthetic-token must not appear in diagnostics"},
        {"failed_records": [{"index": 1}]},
    ],
)
def test_contradictory_success_is_rejected(post, extra):
    post.return_value = response(
        {"code": 200, "status": "OK", "num_records_imported": 2, **extra}
    )
    with pytest.raises(RuntimeError, match="acknowledgement") as error:
        MixpanelService().export_events(CREDENTIALS, EVENTS)
    assert "synthetic-token" not in str(error.value)


def test_partial_import_raises_without_retry_or_exposing_response(post):
    post.return_value = response(
        {
            "code": 400,
            "status": "Bad Request",
            "num_records_imported": 1,
            "error": "synthetic-private-payload",
            "failed_records": [{"index": 1, "message": "synthetic-private-payload"}],
        },
        status=400,
    )
    with pytest.raises(requests.HTTPError, match="accepted 1 of 2") as error:
        MixpanelService().export_events(CREDENTIALS, EVENTS)
    assert error.value.response.status_code == 400
    assert "synthetic-private-payload" not in str(error.value)
    post.assert_called_once()


@pytest.mark.parametrize("status", [401, 403, 413, 429, 500, 503])
def test_http_errors_preserve_status_for_sync_handling(post, status):
    post.return_value = response({"error": "synthetic-private-payload"}, status=status)
    with pytest.raises(requests.HTTPError) as error:
        MixpanelService().export_events(CREDENTIALS, EVENTS)
    assert error.value.response.status_code == status
    assert "synthetic-private-payload" not in str(error.value)
    post.assert_called_once()


def test_malformed_json_fails_closed(post):
    post.return_value = response({})
    post.return_value._content = b"synthetic-private-payload"
    with pytest.raises(RuntimeError, match="acknowledgement") as error:
        MixpanelService().export_events(CREDENTIALS, EVENTS)
    assert "synthetic-private-payload" not in str(error.value)


def test_duplicate_exports_keep_insert_ids(post):
    post.return_value = acknowledged(2)
    service = MixpanelService()
    service.export_events(CREDENTIALS, EVENTS)
    service.export_events(CREDENTIALS, EVENTS)
    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert first == second
    assert first[0]["properties"]["$insert_id"] != first[1]["properties"]["$insert_id"]


def test_fallback_insert_id_is_stable_and_explicit_id_preserved(post):
    post.return_value = acknowledged(2)
    events = [
        {"event": "test", "time": 1700000000, "properties": {"a": 1, "b": 2}},
        {
            "event": "test",
            "time": 1700000000,
            "properties": {"$insert_id": "existing-id"},
        },
    ]
    service = MixpanelService()
    service.export_events(CREDENTIALS, events)
    events[0]["properties"] = {"b": 2, "a": 1}
    service.export_events(CREDENTIALS, events)
    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert first == second
    assert first[1]["properties"]["$insert_id"] == "existing-id"


def test_validation_checks_import_authentication(post):
    post.return_value = acknowledged(1)
    assert MixpanelService().validate_credentials("", CREDENTIALS)["valid"] is True
    assert post.call_args.args == (MixpanelService.IMPORT_URL,)
    assert post.call_args.kwargs["json"][0]["event"] == "$agentcc_connection_test"


@pytest.mark.parametrize("status", [401, 403])
def test_validation_rejects_invalid_authentication(post, status):
    post.return_value = response({"error": "synthetic-private-payload"}, status=status)
    result = MixpanelService().validate_credentials("", CREDENTIALS)
    assert result["valid"] is False
    assert "synthetic-private-payload" not in result["error"]


def test_empty_export_does_not_send(post):
    assert MixpanelService().export_events({}, []) == {"sent": 0}
    post.assert_not_called()


def test_missing_token_does_not_validate(post):
    assert MixpanelService().validate_credentials("", {})["valid"] is False
    post.assert_not_called()


def test_batches_are_bounded_and_all_acknowledged(post):
    post.side_effect = [acknowledged(500), acknowledged(1)]
    assert MixpanelService().export_events(CREDENTIALS, [EVENTS[0]] * 501) == {
        "sent": 501
    }
    assert [len(call.kwargs["json"]) for call in post.call_args_list] == [500, 1]


def test_later_batch_failure_does_not_return_success(post):
    post.side_effect = [
        acknowledged(500),
        response({"num_records_imported": 0}, status=400),
    ]
    with pytest.raises(requests.HTTPError):
        MixpanelService().export_events(CREDENTIALS, [EVENTS[0]] * 501)
    assert post.call_count == 2


def test_local_http_wire_uses_authenticated_strict_import(monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), payload))
            body = json.dumps(
                {"code": 200, "status": "OK", "num_records_imported": len(payload)}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(
        MixpanelService, "IMPORT_URL", f"http://127.0.0.1:{server.server_port}/import"
    )
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    try:
        assert MixpanelService().export_events(CREDENTIALS, EVENTS) == {"sent": 2}
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    path, headers, payload = received[0]
    assert path == "/import?strict=1"
    assert headers["Content-Type"] == "application/json"
    assert (
        base64.b64decode(headers["Authorization"].split(" ")[1]).decode()
        == "synthetic-project-token:"
    )
    assert len(payload) == 2
    assert all("$insert_id" in item["properties"] for item in payload)
    assert "synthetic-project-token" not in json.dumps(payload)

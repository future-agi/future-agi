"""The Guardrails > Test tab reports what happened.

The gateway answers a check that triggered without blocking with 2xx,
x-agentcc-guardrail-triggered: true, x-agentcc-guardrail-action and
x-agentcc-guardrails-triggered (the check names), and a block with 403 and
error.type guardrail_error. test-playground reads a warn from a 2xx with the
trigger header and a block from a 403 guardrail_error only.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from agentcc.services.gateway_client import GatewayClient
from agentcc.tests.console_support import GATEWAY, FakeGateway

TEST_PLAYGROUND = f"{GATEWAY}/test-playground/"
CHAT_OK = {"choices": [{"message": {"role": "assistant", "content": "Paris."}}]}


def _guardrail_block(code="content_blocked"):
    return {
        "error": {
            "message": "Request blocked by guardrail pii-detection",
            "type": "guardrail_error",
            "param": None,
            "code": code,
        }
    }


def _run(auth_client, chat):
    fake = FakeGateway(chat=chat)
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        response = auth_client.post(
            TEST_PLAYGROUND,
            {"prompt": "hello", "model": "gpt-4o-mini"},
            format="json",
        )
    assert response.status_code == 200, response.content
    assert ("revoke_key", "key_playground") in fake.calls
    return response.json()["result"]


@pytest.mark.integration
def test_a_warn_is_warned_with_the_check_names(auth_client):
    result = _run(
        auth_client,
        (
            200,
            CHAT_OK,
            {
                "x-agentcc-guardrail-triggered": "true",
                "x-agentcc-guardrail-action": "warn",
                "x-agentcc-guardrails-triggered": "pii-detection,secret-detection",
            },
        ),
    )

    assert result["warned"] is True
    assert result["blocked"] is False
    assert result["guardrail_action"] == "warn"
    assert result["triggered_checks"] == ["pii-detection", "secret-detection"]
    assert result["status_code"] == 200


@pytest.mark.integration
def test_a_trigger_without_names_is_still_warned(auth_client):
    # A gateway that sends the trigger header without names or action.
    result = _run(
        auth_client, (200, CHAT_OK, {"x-agentcc-guardrail-triggered": "true"})
    )

    assert result["warned"] is True
    assert result["blocked"] is False
    assert result["triggered_checks"] == []
    assert result["guardrail_action"] is None


@pytest.mark.integration
def test_a_log_action_is_reported_as_log(auth_client):
    result = _run(
        auth_client,
        (
            200,
            CHAT_OK,
            {
                "x-agentcc-guardrail-triggered": "true",
                "x-agentcc-guardrail-action": "log",
                "x-agentcc-guardrails-triggered": "topic-restriction",
            },
        ),
    )

    assert result["warned"] is True
    assert result["guardrail_action"] == "log"
    assert result["triggered_checks"] == ["topic-restriction"]


@pytest.mark.integration
def test_a_clean_answer_is_neither(auth_client):
    result = _run(auth_client, (200, CHAT_OK, {"x-agentcc-latency-ms": "12"}))

    assert result["warned"] is False
    assert result["blocked"] is False
    assert result["triggered_checks"] == []
    assert result["guardrail_action"] is None


@pytest.mark.integration
def test_a_guardrail_block_is_blocked(auth_client):
    result = _run(auth_client, (403, _guardrail_block(), {}))

    assert result["blocked"] is True
    assert result["warned"] is False
    assert result["status_code"] == 403


@pytest.mark.integration
def test_a_403_that_is_not_a_guardrail_is_not_a_block(auth_client):
    body = {
        "error": {
            "message": "model not allowed for this key",
            "type": "permission_error",
            "code": "model_not_allowed",
        }
    }
    result = _run(auth_client, (403, body, {}))

    assert result["blocked"] is False
    assert result["warned"] is False


@pytest.mark.integration
@pytest.mark.parametrize("status_code", [429, 500, 502])
def test_provider_failures_are_neither(auth_client, status_code):
    body = {"error": {"message": "upstream failed", "type": "upstream_error"}}
    result = _run(auth_client, (status_code, body, {}))

    assert result["blocked"] is False
    assert result["warned"] is False
    assert result["status_code"] == status_code


@pytest.mark.integration
def test_the_codes_246_and_446_mean_nothing(auth_client):
    warned = _run(auth_client, (246, CHAT_OK, {}))
    blocked = _run(auth_client, (446, _guardrail_block(), {}))

    assert warned["warned"] is False
    assert blocked["blocked"] is False


@pytest.mark.integration
def test_a_triggered_header_on_an_error_is_not_a_warn(auth_client):
    # warned is 2xx AND triggered: a failed answer never reads as a warn.
    result = _run(
        auth_client, (502, {"error": {}}, {"x-agentcc-guardrail-triggered": "true"})
    )

    assert result["warned"] is False


@pytest.mark.integration
def test_header_names_are_read_in_any_case(auth_client):
    result = _run(
        auth_client,
        (
            200,
            CHAT_OK,
            {
                "X-Agentcc-Guardrail-Triggered": "TRUE",
                "X-Agentcc-Guardrail-Action": "Warn",
                "X-Agentcc-Guardrails-Triggered": " secret-detection , pii-detection ,",
            },
        ),
    )

    assert result["warned"] is True
    assert result["guardrail_action"] == "warn"
    assert result["triggered_checks"] == ["pii-detection", "secret-detection"]


# --- the real client against a loopback gateway ---


class _LoopbackGateway(BaseHTTPRequestHandler):
    """POST /-/keys, DELETE /-/keys/{id} and POST /v1/chat/completions, the
    chat answered as the gateway answers a warn: 200 and the guardrail headers."""

    seen = []

    def _send(self, status, body, headers=()):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802 (http.server's name)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.seen.append(("POST", self.path))
        if self.path == "/-/keys":
            self._send(201, {"id": "key_loopback", "key": "sk-loopback-test"})
        elif self.path == "/v1/chat/completions":
            self._send(
                200,
                CHAT_OK,
                (
                    ("X-Agentcc-Guardrail-Triggered", "true"),
                    ("X-Agentcc-Guardrail-Action", "warn"),
                    ("X-Agentcc-Guardrails-Triggered", "pii-detection"),
                ),
            )
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):  # noqa: N802
        self.seen.append(("DELETE", self.path))
        self._send(200, {})

    def log_message(self, *args):
        pass


@pytest.fixture
def loopback_gateway():
    _LoopbackGateway.seen = []
    server = HTTPServer(("127.0.0.1", 0), _LoopbackGateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.integration
def test_the_real_client_reads_the_guardrail_headers(auth_client, loopback_gateway):
    client = GatewayClient(loopback_gateway, admin_token="loopback-admin-token")
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
        response = auth_client.post(
            TEST_PLAYGROUND,
            {"prompt": "my email is a@b.c", "model": "gpt-4o-mini"},
            format="json",
        )

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["warned"] is True
    assert result["blocked"] is False
    assert result["guardrail_action"] == "warn"
    assert result["triggered_checks"] == ["pii-detection"]
    assert ("DELETE", "/-/keys/key_loopback") in _LoopbackGateway.seen

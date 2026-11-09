"""Guardrail test requests refuse while delegation is unavailable.

With explicit opt-in, the same K3 response scenarios still distinguish warns,
blocks and upstream errors. Neither state creates or revokes an API key.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from accounts.models import OrgApiKey
from agentcc.models import AgentccAPIKey
from agentcc.services.gateway_client import GatewayClient
from agentcc.tests.r7_console_a_support import GATEWAY

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


def _key_counts():
    return OrgApiKey._base_manager.count(), AgentccAPIKey._base_manager.count()


def _off_then_enabled(auth_client, client, payload):
    before = _key_counts()
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client) as get:
        with override_settings(AGENTCC_DELEGATED_PLAYGROUND_ENABLED=False):
            off = auth_client.post(TEST_PLAYGROUND, payload, format="json")
        # Assert the OFF contract before trying the opt-in leg. Removing the
        # availability hunk must fail here, not on an incidental mock error.
        assert off.status_code == 409, off.content
        assert off.json()["code"] == "feature_unavailable_733"
        assert off.json()["known_issue"] == "KI-733-PG"
        assert off.json()["result"] == off.json()["message"]
        assert off.json()["result"].startswith("Unavailable in this release:")
        get.assert_not_called()
        assert _key_counts() == before
        with override_settings(AGENTCC_DELEGATED_PLAYGROUND_ENABLED=True):
            response = auth_client.post(TEST_PLAYGROUND, payload, format="json")
        get.assert_called_once_with()
    assert response.status_code == 200, response.content
    assert _key_counts() == before
    return off, response.json()["result"]


def _run(auth_client, chat):
    fake = MagicMock(spec=GatewayClient)
    fake.send_delegated_chat_completion.return_value = chat
    off, result = _off_then_enabled(
        auth_client, fake, {"prompt": "hello", "model": "gpt-4o-mini"}
    )
    fake.send_delegated_chat_completion.assert_called_once()
    fake.create_key.assert_not_called()
    fake.revoke_key.assert_not_called()
    fake.send_chat_completion.assert_not_called()
    return off, result


@pytest.mark.integration
def test_a_warn_is_warned_with_the_check_names(auth_client):
    off, result = _run(
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

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is True
    assert result["blocked"] is False
    assert result["guardrail_action"] == "warn"
    assert result["triggered_checks"] == ["pii-detection", "secret-detection"]
    assert result["status_code"] == 200


@pytest.mark.integration
def test_a_trigger_without_names_is_still_warned(auth_client):
    # A gateway without named-check headers: triggered, no names, no action.
    off, result = _run(
        auth_client, (200, CHAT_OK, {"x-agentcc-guardrail-triggered": "true"})
    )

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is True
    assert result["blocked"] is False
    assert result["triggered_checks"] == []
    assert result["guardrail_action"] is None


@pytest.mark.integration
def test_a_log_action_is_reported_as_log(auth_client):
    off, result = _run(
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

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is True
    assert result["guardrail_action"] == "log"
    assert result["triggered_checks"] == ["topic-restriction"]


@pytest.mark.integration
def test_a_clean_answer_is_neither(auth_client):
    off, result = _run(auth_client, (200, CHAT_OK, {"x-agentcc-latency-ms": "12"}))

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is False
    assert result["blocked"] is False
    assert result["triggered_checks"] == []
    assert result["guardrail_action"] is None


@pytest.mark.integration
def test_a_guardrail_block_is_blocked(auth_client):
    off, result = _run(auth_client, (403, _guardrail_block(), {}))

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
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
    off, result = _run(auth_client, (403, body, {}))

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["blocked"] is False
    assert result["warned"] is False


@pytest.mark.integration
@pytest.mark.parametrize("status_code", [429, 500, 502])
def test_provider_failures_are_neither(auth_client, status_code):
    body = {"error": {"message": "upstream failed", "type": "upstream_error"}}
    off, result = _run(auth_client, (status_code, body, {}))

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["blocked"] is False
    assert result["warned"] is False
    assert result["status_code"] == status_code


@pytest.mark.integration
def test_the_codes_246_and_446_mean_nothing(auth_client):
    off_warn, warned = _run(auth_client, (246, CHAT_OK, {}))
    off_block, blocked = _run(auth_client, (446, _guardrail_block(), {}))

    assert off_warn.status_code == 409
    assert off_warn.json()["code"] == "feature_unavailable_733"
    assert off_block.status_code == 409
    assert off_block.json()["code"] == "feature_unavailable_733"
    assert warned["warned"] is False
    assert blocked["blocked"] is False


@pytest.mark.integration
def test_a_triggered_header_on_an_error_is_not_a_warn(auth_client):
    # warned is 2xx AND triggered: a failed answer never reads as a warn.
    off, result = _run(
        auth_client, (502, {"error": {}}, {"x-agentcc-guardrail-triggered": "true"})
    )

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is False


@pytest.mark.integration
def test_header_names_are_read_in_any_case(auth_client):
    off, result = _run(
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

    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is True
    assert result["guardrail_action"] == "warn"
    assert result["triggered_checks"] == ["pii-detection", "secret-detection"]


# The real delegated client against a loopback gateway that speaks K3.
class _K3Gateway(BaseHTTPRequestHandler):
    """Record every request; only the delegated chat route returns a warn."""

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
        if self.path == "/-/delegated/v1/chat/completions":
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
        self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


@pytest.fixture
def loopback_gateway():
    _K3Gateway.seen = []
    server = HTTPServer(("127.0.0.1", 0), _K3Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.integration
def test_the_real_client_reads_the_k3_headers(auth_client, loopback_gateway):
    client = GatewayClient(
        loopback_gateway, admin_token="test_only_guardrail_admin_pair_00000000"
    )
    with (
        patch.object(client, "create_key", wraps=client.create_key) as create,
        patch.object(client, "revoke_key", wraps=client.revoke_key) as revoke,
        patch.object(
            client,
            "send_delegated_chat_completion",
            wraps=client.send_delegated_chat_completion,
        ) as delegated,
    ):
        off, result = _off_then_enabled(
            auth_client,
            client,
            {"prompt": "my email is a@b.c", "model": "gpt-4o-mini"},
        )
    assert off.status_code == 409
    assert off.json()["code"] == "feature_unavailable_733"
    assert result["warned"] is True
    assert result["blocked"] is False
    assert result["guardrail_action"] == "warn"
    assert result["triggered_checks"] == ["pii-detection"]
    delegated.assert_called_once()
    create.assert_not_called()
    revoke.assert_not_called()
    assert _K3Gateway.seen == [("POST", "/-/delegated/v1/chat/completions")]

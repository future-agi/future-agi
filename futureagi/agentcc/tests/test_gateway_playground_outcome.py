"""test-playground reports whether the gateway blocked or let a guardrail hit through."""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

TRIGGERED = {"x-agentcc-guardrail-triggered": "true"}
CHAT_OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
# A block, as the gateway answers it: 403 with error type guardrail_error.
GUARDRAIL_BLOCK = {
    "error": {
        "message": "Request blocked by guardrail: pii-detection",
        "type": "guardrail_error",
        "code": "content_blocked",
    }
}
PROVIDER_ERROR = {"error": {"message": "upstream failed", "type": "provider_error"}}


@pytest.fixture(autouse=True)
def delegated_playground(settings):
    # Try it runs on the signed-in person's delegation, which is
    # off by default; these cases run with it on.
    settings.AGENTCC_DELEGATED_PLAYGROUND_ENABLED = True
    settings.SECRET_KEY = "test_only_playground_signing_pair_00000000"


def _run_playground(auth_client, status_code, body, headers):
    client = MagicMock()
    client.send_delegated_chat_completion.return_value = (status_code, body, headers)
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
        response = auth_client.post(
            "/agentcc/gateways/default/test-playground/",
            {"prompt": "hello", "model": "gpt-4o-mini"},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.json()
    client.send_delegated_chat_completion.assert_called_once()
    result = response.json()["result"]
    assert result["status_code"] == status_code
    return result["blocked"], result["warned"]


@pytest.mark.integration
@pytest.mark.api
class TestPlaygroundOutcome:
    @pytest.mark.parametrize(
        ("status_code", "body", "headers", "expected"),
        [
            # A warn or log hit: the gateway answers 200 and marks it.
            (200, CHAT_OK, TRIGGERED, (False, True)),
            (200, CHAT_OK, {"X-Agentcc-Guardrail-Triggered": "true"}, (False, True)),
            # Nothing triggered.
            (200, CHAT_OK, {}, (False, False)),
            (200, CHAT_OK, {"x-agentcc-guardrail-triggered": "false"}, (False, False)),
            # A block is a block, whatever the header says, and never a warning.
            (403, GUARDRAIL_BLOCK, TRIGGERED, (True, False)),
            # A 403 that is not a guardrail block (a provider refusal) is neither.
            (403, PROVIDER_ERROR, {}, (False, False)),
            # A provider error is not a guardrail warning.
            (502, PROVIDER_ERROR, TRIGGERED, (False, False)),
        ],
    )
    def test_reports_blocked_and_warned(
        self, auth_client, status_code, body, headers, expected
    ):
        assert _run_playground(auth_client, status_code, body, headers) == expected

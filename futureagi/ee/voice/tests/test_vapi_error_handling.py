"""
Unit tests for VapiService centralized error handling.

Run with: pytest simulate/tests/test_vapi_error_handling.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from ee.voice.exceptions import VapiApiError
from ee.voice.services.vapi_service import (
    _VAPI_DEFAULT_ERROR_MESSAGE,
    VAPI_STATUS_MESSAGES,
    VapiService,
)
from ee.voice.services.types.voice import GetCallInput


@pytest.fixture
def vapi_service():
    """VapiService with a dummy key (no real API calls)."""
    with patch.dict(
        "os.environ",
        {"VAPI_API_KEY": "test-key", "VAPI_API_BASE_URL": "https://api.vapi.ai"},
    ):
        return VapiService(api_key="test-key")


def _mock_response(
    status_code: int, text: str = "", json_body: dict | None = None
) -> MagicMock:
    """Helper to create a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    if text:
        mock.text = text
        try:
            import json as _json

            mock.json.return_value = _json.loads(text)
        except (ValueError, TypeError):
            mock.json.side_effect = ValueError("No JSON")
    elif json_body is not None:
        import json as _json

        mock.text = _json.dumps(json_body)
        mock.json.return_value = json_body
    else:
        mock.text = f'{{"statusCode": {status_code}}}'
        mock.json.return_value = {"statusCode": status_code}
    return mock


class TestHandleErrorResponse:
    """Tests for the _handle_error_response helper."""

    @pytest.mark.parametrize(
        "status_code,expected_fragment",
        [
            (400, "Invalid request"),
            (401, "Authentication failed"),
            (403, "Access denied"),
            (404, "not found"),
            (409, "conflict"),
            (422, "could not be processed"),
            (500, "internal error"),
            (502, "temporary outage"),
            (503, "temporary unavailability"),
        ],
    )
    def test_status_code_message_mapping(
        self, vapi_service, status_code, expected_fragment
    ):
        mock_resp = _mock_response(status_code)

        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(mock_resp, "test action")

        assert expected_fragment.lower() in str(exc_info.value).lower()
        assert exc_info.value.status_code == status_code
        assert exc_info.value.action == "test action"

    def test_unknown_status_code_uses_default_message(self, vapi_service):
        mock_resp = _mock_response(418)

        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(mock_resp, "test action")

        assert _VAPI_DEFAULT_ERROR_MESSAGE in str(exc_info.value)
        assert exc_info.value.status_code == 418

    def test_response_body_preserved_on_exception(self, vapi_service):
        raw_body = '{"statusCode": 500, "message": "Internal server error"}'
        mock_resp = _mock_response(500, text=raw_body)

        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(mock_resp, "create assistant")

        assert exc_info.value.response_body == raw_body
        assert "Internal server error" not in str(exc_info.value)

    def test_provider_message_not_used_by_default(self, vapi_service):
        mock_resp = _mock_response(
            401, text='{"statusCode":401,"message":"Invalid API Key"}'
        )

        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(mock_resp, "create call")

        user_msg = str(exc_info.value)
        assert "Invalid API Key" not in user_msg
        assert "Authentication failed" in user_msg
        assert "create call" in user_msg

    def test_action_string_in_message(self, vapi_service):
        mock_resp = _mock_response(500)

        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(mock_resp, "list phone numbers")

        assert "list phone numbers" in str(exc_info.value)

    def test_vapi_api_error_is_exception_subclass(self):
        err = VapiApiError(
            message="test", status_code=500, action="test", response_body=""
        )
        assert isinstance(err, Exception)


class TestMethodErrorIntegration:
    """Integration tests verifying individual methods use centralized error handler."""

    def test_create_assistant_raises_vapi_error(self, vapi_service):
        mock_resp = _mock_response(401)

        with patch.object(
            vapi_service, "_make_api_request_with_retry", return_value=mock_resp
        ):
            with pytest.raises(VapiApiError) as exc_info:
                vapi_service.create_assistant(name="test", system_prompt="test")

            assert exc_info.value.status_code == 401
            assert "Authentication failed" in str(exc_info.value)

    def test_get_call_raises_vapi_error(self, vapi_service):
        mock_resp = _mock_response(404)

        with patch.object(
            vapi_service, "_make_api_request_with_retry", return_value=mock_resp
        ):
            with pytest.raises(VapiApiError) as exc_info:
                vapi_service.get_call(GetCallInput(call_id="nonexistent-id"))

            assert exc_info.value.status_code == 404
            assert "not found" in str(exc_info.value).lower()

    def test_create_phone_call_raises_vapi_error(self, vapi_service):
        # Mock create_assistant to return a valid response first
        mock_assistant_resp = MagicMock()
        mock_assistant_resp.status_code = 201
        mock_assistant_resp.json.return_value = {"id": "ast-123"}

        mock_call_resp = _mock_response(500)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_assistant_resp
            return mock_call_resp

        with patch.object(
            vapi_service, "_make_api_request_with_retry", side_effect=side_effect
        ):
            with pytest.raises(VapiApiError) as exc_info:
                vapi_service.create_phone_call(
                    phone_number_id="pn-123",
                    to_number="+15551234567",
                    system_prompt="test",
                )

            assert exc_info.value.status_code == 500
            assert "internal error" in str(exc_info.value).lower()

    def test_list_assistants_raises_vapi_error(self, vapi_service):
        mock_resp = _mock_response(403)

        with patch.object(
            vapi_service, "_make_api_request_with_retry", return_value=mock_resp
        ):
            with pytest.raises(VapiApiError) as exc_info:
                vapi_service.list_assistants()

            assert exc_info.value.status_code == 403
            assert "Access denied" in str(exc_info.value)

    def test_vapi_error_caught_by_except_exception(self, vapi_service):
        """Verify VapiApiError is caught by generic except Exception blocks."""
        mock_resp = _mock_response(500)

        with patch.object(
            vapi_service, "_make_api_request_with_retry", return_value=mock_resp
        ):
            try:
                vapi_service.get_call(GetCallInput(call_id="test-id"))
            except Exception as e:
                assert isinstance(e, VapiApiError)
                assert "internal error" in str(e).lower()


class TestStatusMessageMapping:
    """Tests for the VAPI_STATUS_MESSAGES constant."""

    def test_all_expected_codes_present(self):
        expected_codes = {400, 401, 403, 404, 409, 422, 500, 502, 503}
        assert set(VAPI_STATUS_MESSAGES.keys()) == expected_codes

    def test_429_not_in_mapping(self):
        assert 429 not in VAPI_STATUS_MESSAGES

    def test_all_messages_are_non_empty_strings(self):
        for code, msg in VAPI_STATUS_MESSAGES.items():
            assert isinstance(msg, str), f"Message for {code} is not a string"
            assert len(msg) > 0, f"Message for {code} is empty"


class TestGetProviderMessage:
    """Tests for VapiApiError.get_provider_message()."""

    def test_valid_json_with_message(self):
        err = VapiApiError(
            message="Authentication failed.",
            status_code=401,
            action="create call",
            response_body='{"statusCode": 401, "message": "Invalid API Key"}',
        )
        assert err.get_provider_message() == "Invalid API Key"

    def test_empty_response_body(self):
        err = VapiApiError(
            message="Error", status_code=500, action="test", response_body=""
        )
        assert err.get_provider_message() is None

    def test_invalid_json(self):
        err = VapiApiError(
            message="Error",
            status_code=500,
            action="test",
            response_body="not json at all",
        )
        assert err.get_provider_message() is None

    def test_missing_message_key(self):
        err = VapiApiError(
            message="Error",
            status_code=500,
            action="test",
            response_body='{"statusCode": 500, "error": "something"}',
        )
        assert err.get_provider_message() is None

    def test_non_string_message(self):
        err = VapiApiError(
            message="Error",
            status_code=500,
            action="test",
            response_body='{"message": 12345}',
        )
        assert err.get_provider_message() is None

    def test_whitespace_only_message(self):
        err = VapiApiError(
            message="Error",
            status_code=500,
            action="test",
            response_body='{"message": "   "}',
        )
        assert err.get_provider_message() is None

    def test_whitespace_stripped(self):
        err = VapiApiError(
            message="Error",
            status_code=404,
            action="test",
            response_body='{"message": "  Assistant not found  "}',
        )
        assert err.get_provider_message() == "Assistant not found"


class TestOutboundCallErrorExposure:
    """Tests verifying outbound calls (use_provider_message=True) expose real VAPI errors."""

    def test_provider_message_in_str_e(self, vapi_service):
        """When VAPI returns a JSON body with 'message' and use_provider_message=True, str(e) contains it."""
        mock_resp = _mock_response(
            401, text='{"statusCode": 401, "message": "Invalid API Key"}'
        )
        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(
                mock_resp, "create outbound call", use_provider_message=True
            )

        assert "Invalid API Key" in str(exc_info.value)
        assert exc_info.value.get_provider_message() == "Invalid API Key"

    def test_generic_fallback_when_no_provider_message(self, vapi_service):
        """When response_body has no parseable message, generic status message is used even for outbound."""
        mock_resp = _mock_response(401, text="<html>Gateway Timeout</html>")
        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(
                mock_resp, "create outbound call", use_provider_message=True
            )

        assert "Authentication failed" in str(exc_info.value)

    def test_wallet_balance_error_exposed(self, vapi_service):
        """Real-world case: VAPI wallet balance error surfaces in str(e) for outbound calls."""
        body = (
            '{"statusCode":400,'
            '"message":"Your Wallet Balance is -0.8. Please Purchase More Credits.",'
            '"error":"Bad Request"}'
        )
        mock_resp = _mock_response(400, text=body)
        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(
                mock_resp, "create outbound call", use_provider_message=True
            )

        assert "Wallet Balance" in str(exc_info.value)
        assert "Invalid request" not in str(exc_info.value)

    def test_404_assistant_not_found(self, vapi_service):
        """Common case: user's assistant ID doesn't exist in outbound call."""
        mock_resp = _mock_response(
            404, text='{"statusCode": 404, "message": "Assistant not found"}'
        )
        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(
                mock_resp, "create outbound call", use_provider_message=True
            )

        assert "Assistant not found" in str(exc_info.value)
        assert "not found" not in str(exc_info.value).replace("Assistant not found", "")

    def test_inbound_call_uses_generic_message(self, vapi_service):
        """Inbound calls (default use_provider_message=False) use generic status messages."""
        mock_resp = _mock_response(
            401, text='{"statusCode": 401, "message": "Invalid API Key"}'
        )
        with pytest.raises(VapiApiError) as exc_info:
            vapi_service._handle_error_response(mock_resp, "create call")

        assert "Invalid API Key" not in str(exc_info.value)
        assert "Authentication failed" in str(exc_info.value)

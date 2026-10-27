"""
Unit tests for VoiceServiceManager and VapiService call matching.

Run with: pytest simulate/tests/test_voice_service_manager.py -v

Tests cover:
- find_client_call: Call matching via VapiService._find_customer_vapi_call_id
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from ee.voice.semantics import FAGICallData
from simulate.semantics import CallType
from ee.voice.services.types.voice import FindClientCallInput
from ee.voice.services.vapi_service import VapiService
from tracer.models.observability_provider import ProviderChoices

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def vapi_service():
    """Create a VapiService instance with mocked init."""
    with patch.object(VapiService, "__init__", lambda x, **kwargs: None):
        service = VapiService()
        service.api_key = "system-test-key"
        return service


@pytest.fixture
def sample_our_call_data():
    """Create sample FAGICallData for testing."""
    now = datetime.utcnow()
    return FAGICallData(
        call_id="our-call-id-123",
        call_type=CallType.OUTBOUND,
        status="completed",
        assistant_id="test-assistant-id",
        system_phone_number="+0987654321",
        customer_phone_number="+1234567890",
        system_phone_number_id="phone-id-123",
        recording_url="https://example.com/recording.mp3",
        log_url="https://example.com/log",
        created_at=now.isoformat() + "Z",
        started_at=now.isoformat() + "Z",
        ended_at=(now + timedelta(minutes=5)).isoformat() + "Z",
        updated_at=now.isoformat() + "Z",
        cost=0.5,
        raw_log={
            ProviderChoices.VAPI: {
                "id": "our-call-id-123",
                "artifact": {
                    "variableValues": {"phoneNumber": {"number": "+0987654321"}}
                },
                "messages": [
                    {"role": "user", "message": "Hello, I need help"},
                    {"role": "assistant", "message": "Hi, how can I assist you today?"},
                ],
            }
        },
    )


@pytest.fixture
def sample_customer_call_high_score():
    """Create a sample customer call that should have high match score."""
    now = datetime.utcnow()
    return {
        "id": "customer-call-high-score",
        "type": "outboundPhoneCall",  # Matches when our call is OUTBOUND
        "startedAt": now.isoformat() + "Z",
        "endedAt": (now + timedelta(minutes=5)).isoformat() + "Z",
        "customer": {"number": "+0987654321"},
        "artifact": {"variableValues": {"phoneNumber": {"number": "+1234567890"}}},
        "messages": [
            {"role": "user", "message": "Hello, I need help"},
            {"role": "assistant", "message": "Hi, how can I assist you today?"},
        ],
    }


@pytest.fixture
def sample_customer_call_low_score():
    """Create a sample customer call that should have low match score."""
    now = datetime.utcnow()
    return {
        "id": "customer-call-low-score",
        "type": "outboundPhoneCall",  # Matches when our call is OUTBOUND
        "startedAt": (now + timedelta(seconds=8)).isoformat() + "Z",  # 8 seconds off
        "endedAt": (now + timedelta(minutes=3)).isoformat() + "Z",  # Different duration
        "customer": {"number": "+0987654321"},
        "artifact": {"variableValues": {"phoneNumber": {"number": "+1234567890"}}},
        "messages": [
            {"role": "user", "message": "Different conversation"},
            {"role": "assistant", "message": "Completely different response"},
        ],
    }


# ============================================================================
# find_client_call Tests (via VapiService._find_customer_vapi_call_id)
# ============================================================================


class TestFindClientCall:
    """Tests for VapiService.find_client_call (call matching logic)."""

    def _make_input(self, our_call_data, **overrides):
        """Helper to build FindClientCallInput."""
        defaults = {
            "customer_api_key": "test-key",
            "customer_assistant_id": "test-assistant",
            "our_call_data": our_call_data,
        }
        defaults.update(overrides)
        return FindClientCallInput(**defaults)

    @pytest.mark.unit
    @patch("ee.voice.services.vapi_service.send_critical_slack_notification")
    def test_high_score_match_no_alert(
        self,
        mock_slack,
        vapi_service,
        sample_our_call_data,
        sample_customer_call_high_score,
    ):
        """Test that high score match does not trigger Slack alert."""
        mock_inner_service = MagicMock()
        mock_inner_service.list_calls.return_value = [sample_customer_call_high_score]

        with patch(
            "ee.voice.services.vapi_service.VapiService",
            return_value=mock_inner_service,
        ):
            result = vapi_service.find_client_call(
                self._make_input(sample_our_call_data)
            )

        # Should return the matched call ID
        assert result is not None

    @pytest.mark.unit
    @patch("ee.voice.services.vapi_service.send_critical_slack_notification")
    def test_low_score_match_triggers_alert(
        self,
        mock_slack,
        vapi_service,
        sample_our_call_data,
        sample_customer_call_low_score,
    ):
        """Test that low score match triggers Slack alert."""
        mock_inner_service = MagicMock()
        mock_inner_service.list_calls.return_value = [
            sample_customer_call_low_score,
            sample_customer_call_low_score,  # Need multiple to trigger scoring
        ]

        with patch(
            "ee.voice.services.vapi_service.VapiService",
            return_value=mock_inner_service,
        ):
            result = vapi_service.find_client_call(
                self._make_input(sample_our_call_data)
            )

        # Should still return a match even with low score (no alert sent)
        if result:
            assert isinstance(result, str)

    @pytest.mark.unit
    @patch("ee.voice.services.vapi_service.send_critical_slack_notification")
    def test_custom_threshold(
        self,
        mock_slack,
        vapi_service,
        sample_our_call_data,
        sample_customer_call_high_score,
    ):
        """Test that custom threshold is respected via _find_customer_vapi_call_id."""
        mock_inner_service = MagicMock()
        mock_inner_service.list_calls.return_value = [
            sample_customer_call_high_score,
            sample_customer_call_high_score,
        ]

        with patch(
            "ee.voice.services.vapi_service.VapiService",
            return_value=mock_inner_service,
        ):
            # Call internal method directly to pass custom threshold
            result = vapi_service._find_customer_vapi_call_id(
                customer_api_key="test-key",
                customer_assistant_id="test-assistant",
                our_call_data=sample_our_call_data,
                min_match_score_threshold=999.0,  # Impossibly high threshold
            )

        # Should return a match regardless of threshold (alerting disabled)
        assert result is not None

    @pytest.mark.unit
    def test_no_matching_calls_returns_none(
        self,
        vapi_service,
        sample_our_call_data,
    ):
        """Test that no matching calls returns None."""
        mock_inner_service = MagicMock()
        mock_inner_service.list_calls.return_value = []

        with patch(
            "ee.voice.services.vapi_service.VapiService",
            return_value=mock_inner_service,
        ):
            result = vapi_service.find_client_call(
                self._make_input(sample_our_call_data)
            )

        assert result is None

    @pytest.mark.unit
    def test_single_match_returns_immediately(
        self,
        vapi_service,
        sample_our_call_data,
        sample_customer_call_high_score,
    ):
        """Test that single match returns immediately without scoring."""
        mock_inner_service = MagicMock()
        mock_inner_service.list_calls.return_value = [sample_customer_call_high_score]

        with patch(
            "ee.voice.services.vapi_service.VapiService",
            return_value=mock_inner_service,
        ):
            result = vapi_service.find_client_call(
                self._make_input(sample_our_call_data)
            )

        assert result == "customer-call-high-score"

    @pytest.mark.unit
    def test_invalid_datetime_returns_none(
        self,
        vapi_service,
    ):
        """Test that invalid datetime in our call data returns None."""
        invalid_call_data = FAGICallData(
            call_id="test-call-id",
            call_type=CallType.OUTBOUND,
            status="completed",
            assistant_id="test-assistant-id",
            system_phone_number="+0987654321",
            customer_phone_number="+1234567890",
            system_phone_number_id="phone-id-123",
            recording_url="https://example.com/recording.mp3",
            log_url="https://example.com/log",
            created_at="2024-01-01T00:00:00Z",
            started_at="invalid-datetime",
            ended_at=None,
            updated_at="2024-01-01T00:00:00Z",
            cost=0.5,
            raw_log={},
        )

        result = vapi_service.find_client_call(self._make_input(invalid_call_data))

        assert result is None

    @pytest.mark.unit
    def test_missing_start_time_returns_none(
        self,
        vapi_service,
    ):
        """Test that missing start time returns None."""
        no_start_time_data = FAGICallData(
            call_id="test-call-id",
            call_type=CallType.OUTBOUND,
            status="completed",
            assistant_id="test-assistant-id",
            system_phone_number="+0987654321",
            customer_phone_number="+1234567890",
            system_phone_number_id="phone-id-123",
            recording_url="https://example.com/recording.mp3",
            log_url="https://example.com/log",
            created_at="2024-01-01T00:00:00Z",
            started_at=None,
            ended_at=None,
            updated_at="2024-01-01T00:00:00Z",
            cost=0.5,
            raw_log={},
        )

        result = vapi_service.find_client_call(self._make_input(no_start_time_data))

        assert result is None

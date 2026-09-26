"""
Unit tests for LivekitService (VoiceServiceBlueprint implementation).

Run with: pytest simulate/tests/test_livekit_service.py -v
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ee.voice.services.livekit.config import LiveKitConfig
from ee.voice.services.livekit.service import AGENT_NAME, PROVIDER_KEY, _room_name
from ee.voice.services.types.voice import (
    CallResult,
    CustomerMetrics,
    EndCallInput,
    FindClientCallInput,
    InboundCallInput,
    OutboundCallInput,
    PersistAudioInput,
)
from tracer.models.observability_provider import ProviderChoices

# ============================================================================
# Fixtures
# ============================================================================

MOCK_CONFIG = LiveKitConfig(
    url="ws://localhost:7880",
    api_key="test-api-key",
    api_secret="test-api-secret-min-32-characters",
    sip_inbound_trunk_id="trunk-123",
    sip_outbound_trunk_id="trunk-456",
    egress_bucket="livekit-egress",
    s3_access_key="minioadmin",
    s3_secret_key="minioadmin",
    s3_region="us-east-1",
    backend_callback_url="http://localhost:8000",
)


@pytest.fixture
def livekit_service():
    """Create a LivekitService with mocked config."""
    with patch(
        "ee.voice.services.livekit.service.LiveKitConfig.from_env",
        return_value=MOCK_CONFIG,
    ):
        from ee.voice.services.livekit.service import LivekitService

        return LivekitService()


@pytest.fixture
def mock_lkapi():
    """Create a fully mocked LiveKitAPI."""
    api = AsyncMock()

    # Room service
    api.room.list_rooms = AsyncMock(return_value=MagicMock(rooms=[]))
    api.room.create_room = AsyncMock(
        return_value=MagicMock(sid="room-sid-123", name="call_exec-123")
    )
    api.room.delete_room = AsyncMock()

    # Agent dispatch
    api.agent_dispatch.create_dispatch = AsyncMock(
        return_value=MagicMock(id="dispatch-123")
    )

    # Egress
    api.egress.start_room_composite_egress = AsyncMock(
        return_value=MagicMock(egress_id="egress-123")
    )

    # SIP
    api.sip.create_sip_participant = AsyncMock(
        return_value=MagicMock(participant_id="sip-part-123")
    )

    # Close
    api.aclose = AsyncMock()

    return api


# ============================================================================
# Room naming
# ============================================================================


class TestRoomNaming:
    @pytest.mark.unit
    def test_room_name_format(self):
        assert _room_name("abc-123") == "call_abc-123"

    @pytest.mark.unit
    def test_room_name_deterministic(self):
        assert _room_name("exec-1") == _room_name("exec-1")


# ============================================================================
# initiate_inbound_call
# ============================================================================


class TestInitiateInboundCall:
    @pytest.mark.unit
    def test_success(self, livekit_service, mock_lkapi):
        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            result = livekit_service.initiate_inbound_call(
                InboundCallInput(
                    call_id="exec-123",
                    user_phone_number="+15551234567",
                    system_prompt="You are a test agent.",
                )
            )

        assert result.success is True
        assert result.provider_call_id == "call_exec-123"
        assert result.assistant_id == AGENT_NAME
        assert result.provider_data["room_name"] == "call_exec-123"
        assert result.provider_data["room_sid"] == "room-sid-123"
        assert result.provider_data["participant_id"] == "sip-part-123"
        assert result.provider_data["dispatch_id"] == "dispatch-123"

    @pytest.mark.unit
    def test_dispatches_agent_with_metadata(self, livekit_service, mock_lkapi):
        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            livekit_service.initiate_inbound_call(
                InboundCallInput(
                    call_id="exec-456",
                    user_phone_number="+15551234567",
                    system_prompt="Be helpful.",
                    voice_settings={"language": "es"},
                    metadata={"scenario_id": "s-1"},
                )
            )

        dispatch_call = mock_lkapi.agent_dispatch.create_dispatch
        dispatch_call.assert_awaited_once()
        req = dispatch_call.call_args[0][0]
        assert req.agent_name == AGENT_NAME
        assert req.room == "call_exec-456"
        # Metadata is JSON string containing prompt + settings + extras
        import json

        meta = json.loads(req.metadata)
        assert meta["system_prompt"] == "Be helpful."
        assert meta["voice_settings"]["language"] == "es"
        assert meta["scenario_id"] == "s-1"

    @pytest.mark.unit
    def test_cleans_stale_room_on_rerun(self, livekit_service, mock_lkapi):
        """On rerun, if the old room has participants, delete it first."""
        old_room = MagicMock(name="call_exec-789", num_participants=1, sid="old-sid")
        mock_lkapi.room.list_rooms = AsyncMock(return_value=MagicMock(rooms=[old_room]))

        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            result = livekit_service.initiate_inbound_call(
                InboundCallInput(
                    call_id="exec-789",
                    user_phone_number="+15551234567",
                    system_prompt="Test.",
                )
            )

        assert result.success is True
        # Old room should be deleted before creating new one
        mock_lkapi.room.delete_room.assert_awaited()

    @pytest.mark.unit
    def test_failure_re_raises(self, livekit_service, mock_lkapi):
        mock_lkapi.room.list_rooms = AsyncMock(side_effect=Exception("API down"))

        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            with pytest.raises(Exception, match="API down"):
                livekit_service.initiate_inbound_call(
                    InboundCallInput(
                        call_id="exec-fail",
                        user_phone_number="+15551234567",
                        system_prompt="Test.",
                    )
                )

    @pytest.mark.unit
    def test_cleans_up_room_on_sip_failure(self, livekit_service, mock_lkapi):
        """If SIP dial-out fails, room should be cleaned up and exception re-raised."""
        mock_lkapi.sip.create_sip_participant = AsyncMock(
            side_effect=Exception("SIP trunk error")
        )

        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            with pytest.raises(Exception, match="SIP trunk error"):
                livekit_service.initiate_inbound_call(
                    InboundCallInput(
                        call_id="exec-cleanup",
                        user_phone_number="+15551234567",
                        system_prompt="Test.",
                    )
                )

        mock_lkapi.room.delete_room.assert_awaited()

    @pytest.mark.unit
    def test_closes_api_on_success(self, livekit_service, mock_lkapi):
        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            livekit_service.initiate_inbound_call(
                InboundCallInput(
                    call_id="exec-close",
                    user_phone_number="+15551234567",
                    system_prompt="Test.",
                )
            )

        mock_lkapi.aclose.assert_awaited_once()

    @pytest.mark.unit
    def test_closes_api_on_failure(self, livekit_service, mock_lkapi):
        mock_lkapi.room.list_rooms = AsyncMock(side_effect=Exception("boom"))

        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            with pytest.raises(Exception, match="boom"):
                livekit_service.initiate_inbound_call(
                    InboundCallInput(
                        call_id="exec-fail",
                        user_phone_number="+15551234567",
                        system_prompt="Test.",
                    )
                )

        mock_lkapi.aclose.assert_awaited_once()


# ============================================================================
# initiate_outbound_call
# ============================================================================


class TestInitiateOutboundCall:
    @pytest.mark.unit
    def test_stores_metadata_and_returns_success(self, livekit_service, mock_lkapi):
        mock_call = MagicMock()
        mock_call.provider_call_data = {}
        mock_call.asave = AsyncMock(return_value=None)

        with (
            patch.object(livekit_service, "_create_api", return_value=mock_lkapi),
            patch(
                "simulate.models.test_execution.CallExecution.objects"
            ) as mock_objects,
        ):
            mock_objects.aget = AsyncMock(return_value=mock_call)
            result = livekit_service.initiate_outbound_call(
                OutboundCallInput(
                    call_execution_id="exec-out",
                    system_prompt="Test.",
                    phone_number="+15551234567",
                    provider_phone_id="phone-id-1",
                )
            )

        assert result.success is True
        assert result.phone_number == "+15551234567"
        assert result.phone_number_id == "phone-id-1"
        # Verify metadata was stored on the call execution
        stored = mock_call.provider_call_data[PROVIDER_KEY]
        assert stored["outbound_sip"] is True
        mock_call.asave.assert_awaited_once()

    @pytest.mark.unit
    def test_stores_workflow_id(self, livekit_service, mock_lkapi):
        """Workflow ID from metadata is stored in provider_call_data."""
        mock_call = MagicMock()
        mock_call.provider_call_data = {}
        mock_call.asave = AsyncMock(return_value=None)

        with (
            patch.object(livekit_service, "_create_api", return_value=mock_lkapi),
            patch(
                "simulate.models.test_execution.CallExecution.objects"
            ) as mock_objects,
        ):
            mock_objects.aget = AsyncMock(return_value=mock_call)
            livekit_service.initiate_outbound_call(
                OutboundCallInput(
                    call_execution_id="exec-wf",
                    system_prompt="Test.",
                    phone_number="+15551234567",
                    provider_phone_id="phone-1",
                    metadata={"workflow_id": "call-exec-abc-123"},
                )
            )

        stored = mock_call.provider_call_data[PROVIDER_KEY]
        assert stored["workflow_id"] == "call-exec-abc-123"

    @pytest.mark.unit
    def test_missing_call_execution_returns_failure(self, livekit_service, mock_lkapi):
        """Re-raises CallExecution.DoesNotExist so Temporal can retry."""
        with (
            patch.object(livekit_service, "_create_api", return_value=mock_lkapi),
            patch(
                "simulate.models.test_execution.CallExecution.objects"
            ) as mock_objects,
        ):
            from simulate.models.test_execution import CallExecution

            mock_objects.aget = AsyncMock(side_effect=CallExecution.DoesNotExist)
            with pytest.raises(CallExecution.DoesNotExist):
                livekit_service.initiate_outbound_call(
                    OutboundCallInput(
                        call_execution_id="nonexistent",
                        system_prompt="Test.",
                        phone_number="+15551234567",
                        provider_phone_id="phone-1",
                    )
                )

    @pytest.mark.unit
    def test_preserves_existing_provider_data(self, livekit_service, mock_lkapi):
        """Existing provider_call_data keys are preserved."""
        mock_call = MagicMock()
        mock_call.provider_call_data = {"other_provider": {"key": "value"}}
        mock_call.asave = AsyncMock(return_value=None)

        with (
            patch.object(livekit_service, "_create_api", return_value=mock_lkapi),
            patch(
                "simulate.models.test_execution.CallExecution.objects"
            ) as mock_objects,
        ):
            mock_objects.aget = AsyncMock(return_value=mock_call)
            livekit_service.initiate_outbound_call(
                OutboundCallInput(
                    call_execution_id="exec-preserve",
                    system_prompt="Test.",
                    phone_number="+15551234567",
                    provider_phone_id="phone-1",
                )
            )

        assert mock_call.provider_call_data["other_provider"]["key"] == "value"
        assert PROVIDER_KEY in mock_call.provider_call_data

    @pytest.mark.unit
    def test_voice_settings_stored(self, livekit_service, mock_lkapi):
        """Voice settings are stored in provider_call_data."""
        mock_call = MagicMock()
        mock_call.provider_call_data = {}
        mock_call.asave = AsyncMock(return_value=None)

        voice_settings = {"language": "es", "tts_model": "sonic-2", "voice_id": "abc"}
        with (
            patch.object(livekit_service, "_create_api", return_value=mock_lkapi),
            patch(
                "simulate.models.test_execution.CallExecution.objects"
            ) as mock_objects,
        ):
            mock_objects.aget = AsyncMock(return_value=mock_call)
            livekit_service.initiate_outbound_call(
                OutboundCallInput(
                    call_execution_id="exec-voice",
                    system_prompt="Hola.",
                    phone_number="+15551234567",
                    provider_phone_id="phone-1",
                    voice_settings=voice_settings,
                )
            )

        # voice_settings is passed in dispatch metadata, not stored in
        # provider_call_data — verify via dispatch call.
        dispatch_call = mock_lkapi.agent_dispatch.create_dispatch
        dispatch_call.assert_awaited()
        req = dispatch_call.call_args[0][0]
        meta = json.loads(req.metadata)
        assert meta["voice_settings"]["language"] == "es"
        assert meta["voice_settings"]["tts_model"] == "sonic-2"


# ============================================================================
# end_call
# ============================================================================


class TestEndCall:
    @pytest.mark.unit
    def test_deletes_room(self, livekit_service, mock_lkapi):
        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            result = livekit_service.end_call(
                EndCallInput(provider_call_payload={"room_name": "call_exec-123"})
            )

        assert result is True
        mock_lkapi.room.delete_room.assert_awaited()

    @pytest.mark.unit
    def test_no_room_name_returns_false(self, livekit_service, mock_lkapi):
        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            result = livekit_service.end_call(EndCallInput(provider_call_payload={}))

        assert result is False

    @pytest.mark.unit
    def test_none_payload_returns_false(self, livekit_service, mock_lkapi):
        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            result = livekit_service.end_call(EndCallInput())

        assert result is False

    @pytest.mark.unit
    def test_api_failure_returns_false(self, livekit_service, mock_lkapi):
        mock_lkapi.room.delete_room = AsyncMock(side_effect=Exception("fail"))

        with patch.object(livekit_service, "_create_api", return_value=mock_lkapi):
            result = livekit_service.end_call(
                EndCallInput(provider_call_payload={"room_name": "call_exec-123"})
            )

        assert result is False


# ============================================================================
# find_client_call
# ============================================================================


class TestFindClientCall:
    @pytest.mark.unit
    def test_always_returns_none(self, livekit_service):
        result = livekit_service.find_client_call(
            FindClientCallInput(
                customer_api_key="key",
                customer_assistant_id="asst",
                our_call_data=MagicMock(),
            )
        )
        assert result is None


# ============================================================================
# get_recording_urls
# ============================================================================


class TestGetRecordingUrls:
    @pytest.mark.unit
    def test_extracts_recording_urls(self, livekit_service):
        payload = {"recording_urls": {"combined": "http://s3/recording.ogg"}}
        result = livekit_service.get_recording_urls(payload)
        assert result == {"combined": "http://s3/recording.ogg"}

    @pytest.mark.unit
    def test_none_payload_returns_empty(self, livekit_service):
        result = livekit_service.get_recording_urls(None)
        assert result == {}

    @pytest.mark.unit
    def test_missing_key_returns_empty(self, livekit_service):
        result = livekit_service.get_recording_urls({"other": "data"})
        assert result == {}


# ============================================================================
# get_customer_metrics
# ============================================================================


class TestGetCustomerMetrics:
    @pytest.mark.unit
    @patch("litellm.cost_per_token", return_value=(0.05, 0.10))
    @patch(
        "litellm.model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 0.15e-6}},
    )
    def test_builds_from_usage_data(self, _mock_cpt, livekit_service):
        call_data = MagicMock()
        call_data.raw_log = {
            PROVIDER_KEY: {
                "usage": {
                    "stt": {"duration": 60.0},
                    "llm": {"prompt_tokens": 1000, "completion_tokens": 500},
                    "tts": {"characters": 1000},
                    "models": {
                        "stt": "nova-3",
                        "llm": "gpt-4o-mini",
                        "tts": "sonic-2",
                    },
                    "system_metrics": {"latency_p50_ms": 120},
                }
            }
        }

        result = livekit_service.get_customer_metrics(call_data)

        assert isinstance(result, CustomerMetrics)
        assert result.total_cost > 0
        assert "stt" in result.cost_breakdown
        assert "llm" in result.cost_breakdown
        assert "tts" in result.cost_breakdown
        # Each breakdown entry should have a computed "cost" key
        assert "cost" in result.cost_breakdown["stt"]
        assert result.cost_breakdown["stt"]["cost"] > 0
        assert result.system_metrics == {"latency_p50_ms": 120}

    @pytest.mark.unit
    def test_empty_raw_log_returns_zeros(self, livekit_service):
        call_data = MagicMock()
        call_data.raw_log = {}

        result = livekit_service.get_customer_metrics(call_data)

        assert result.total_cost == 0.0
        assert result.cost_breakdown == {}
        assert result.system_metrics is None

    @pytest.mark.unit
    def test_none_raw_log_returns_zeros(self, livekit_service):
        call_data = MagicMock()
        call_data.raw_log = None

        result = livekit_service.get_customer_metrics(call_data)

        assert result.total_cost == 0.0


# ============================================================================
# extract_costs
# ============================================================================


class TestExtractCosts:
    @pytest.mark.unit
    @patch("litellm.cost_per_token", return_value=(0.05, 0.10))
    @patch(
        "litellm.model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 0.15e-6}},
    )
    def test_computes_costs_from_usage(self, _mock_cpt, livekit_service):
        import asyncio

        from ee.voice.services.types.voice import CostBreakdown

        mock_call = MagicMock()
        mock_call.provider_call_data = {
            PROVIDER_KEY: {
                "usage": {
                    "stt": {"duration": 60.0},
                    "llm": {"prompt_tokens": 1000, "completion_tokens": 500},
                    "tts": {"characters": 1000},
                    "models": {
                        "stt": "nova-3",
                        "llm": "gpt-4o-mini",
                        "tts": "sonic-2",
                    },
                }
            }
        }
        mock_call.duration_seconds = 60.0

        with patch(
            "simulate.models.test_execution.CallExecution.objects"
        ) as mock_objects:
            mock_objects.aget = AsyncMock(return_value=mock_call)
            result = asyncio.run(livekit_service.extract_costs("exec-cost"))

        assert isinstance(result, CostBreakdown)
        assert result.total > 0
        assert result.stt > 0
        assert result.llm > 0
        assert result.tts > 0
        assert result.storage >= 0

    @pytest.mark.unit
    def test_empty_usage_returns_zeros(self, livekit_service):
        import asyncio

        from ee.voice.services.types.voice import CostBreakdown

        mock_call = MagicMock()
        mock_call.provider_call_data = {PROVIDER_KEY: {}}

        with patch(
            "simulate.models.test_execution.CallExecution.objects"
        ) as mock_objects:
            mock_objects.aget = AsyncMock(return_value=mock_call)
            result = asyncio.run(livekit_service.extract_costs("exec-empty"))

        assert isinstance(result, CostBreakdown)
        assert result.total == 0.0
        assert result.stt == 0.0
        assert result.llm == 0.0
        assert result.tts == 0.0

    @pytest.mark.unit
    def test_none_provider_data_returns_zeros(self, livekit_service):
        import asyncio

        from ee.voice.services.types.voice import CostBreakdown

        mock_call = MagicMock()
        mock_call.provider_call_data = None

        with patch(
            "simulate.models.test_execution.CallExecution.objects"
        ) as mock_objects:
            mock_objects.aget = AsyncMock(return_value=mock_call)
            result = asyncio.run(livekit_service.extract_costs("exec-none"))

        assert isinstance(result, CostBreakdown)
        assert result.total == 0.0


# ============================================================================
# Pricing module
# ============================================================================


class TestPricing:
    @pytest.mark.unit
    def test_stt_cost(self):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import RATES, calculate_stt_cost

        # 60 seconds at default rate
        cost = calculate_stt_cost(60.0, "nova-3", RATES)
        # $0.000128/sec × 60 = $0.00768
        assert cost == pytest.approx(Decimal("0.00768"), abs=Decimal("0.0001"))

    @pytest.mark.unit
    def test_tts_cost(self):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import RATES, calculate_tts_cost

        # 1000 characters at default rate
        cost = calculate_tts_cost(1000, "sonic-2", RATES)
        # $0.000011/char × 1000 = $0.011
        assert cost == pytest.approx(Decimal("0.011"), abs=Decimal("0.001"))

    @pytest.mark.unit
    @patch(
        "litellm.cost_per_token",
        return_value=(0.00015, 0.0003),
    )
    @patch(
        "litellm.model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 0.15e-6}},
    )
    def test_llm_cost_with_litellm(self, _mock_cpt):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import calculate_llm_cost

        cost = calculate_llm_cost(1000, 500, "gpt-4o-mini")
        # Mocked: 0.00015 + 0.0003 = 0.00045
        assert cost == pytest.approx(Decimal("0.00045"), abs=Decimal("0.0001"))

    @pytest.mark.unit
    @patch("litellm.model_cost", {})
    def test_llm_cost_unknown_model_returns_zero(self):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import calculate_llm_cost

        cost = calculate_llm_cost(1000, 500, "unknown-model")
        assert cost == Decimal("0")

    @pytest.mark.unit
    def test_storage_cost(self):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import RATES, calculate_storage_cost

        # 60 seconds of audio
        cost = calculate_storage_cost(60.0, RATES)
        assert cost > Decimal("0")

    @pytest.mark.unit
    def test_storage_cost_zero_duration(self):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import RATES, calculate_storage_cost

        cost = calculate_storage_cost(0.0, RATES)
        assert cost == Decimal("0")

    @pytest.mark.unit
    @patch("litellm.cost_per_token", return_value=(0.05, 0.10))
    @patch(
        "litellm.model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 0.15e-6}},
    )
    def test_calculate_call_costs_full(self, _mock_cpt):
        from ee.voice.services.livekit.pricing import calculate_call_costs

        usage = {
            "stt": {"duration": 120.0},
            "llm": {"prompt_tokens": 2000, "completion_tokens": 1000},
            "tts": {"characters": 5000},
            "models": {"stt": "nova-3", "llm": "gpt-4o-mini", "tts": "sonic-2"},
        }
        costs = calculate_call_costs(usage, duration_seconds=120.0)

        assert costs["total"] > 0
        assert (
            costs["total"]
            == costs["stt"] + costs["llm"] + costs["tts"] + costs["storage"]
        )
        assert costs["stt"] > 0
        assert costs["llm"] > 0
        assert costs["tts"] > 0
        assert costs["storage"] > 0

    @pytest.mark.unit
    def test_calculate_call_costs_empty_usage(self):
        from decimal import Decimal

        from ee.voice.services.livekit.pricing import calculate_call_costs

        costs = calculate_call_costs({})
        assert costs["total"] == Decimal("0")

    @pytest.mark.unit
    def test_pricing_rates_are_frozen(self):
        from ee.voice.services.livekit.pricing import PricingRates

        rates = PricingRates()
        with pytest.raises(AttributeError):
            rates.stt_cost_per_second = 999  # type: ignore[misc]


# ============================================================================
# Engine registry
# ============================================================================


class TestEngineRegistry:
    @pytest.mark.unit
    def test_registry_resolves_livekit(self):
        from ee.voice.services.livekit import LivekitService
        from ee.voice.services.voice_service_manager import VoiceServiceManager

        assert ProviderChoices.LIVEKIT in VoiceServiceManager.ENGINE_REGISTRY
        assert (
            VoiceServiceManager.ENGINE_REGISTRY[ProviderChoices.LIVEKIT]
            is LivekitService
        )

    @pytest.mark.unit
    def test_vsm_instantiates_livekit_engine(self):
        from ee.voice.services.livekit.service import LivekitService

        with patch(
            "ee.voice.services.livekit.service.LiveKitConfig.from_env",
            return_value=MOCK_CONFIG,
        ):
            from ee.voice.services.voice_service_manager import VoiceServiceManager

            vsm = VoiceServiceManager(system_voice_provider=ProviderChoices.LIVEKIT)

        assert isinstance(vsm.engine, LivekitService)


# ============================================================================
# Provider key uses enum
# ============================================================================


class TestValidateApiKey:
    @pytest.mark.unit
    def test_returns_true(self, livekit_service):
        assert livekit_service.validate_api_key() is True


# ============================================================================
# get_call / get_call_async
# ============================================================================


class TestGetCall:
    def _mock_call_execution(self, **overrides):
        defaults = dict(
            service_provider_call_id="call_exec-100",
            call_type="inbound",
            status="completed",
            ended_reason=None,
            call_summary="Test summary",
            recording_url="http://s3/rec.ogg",
            cost_cents=150,
            duration_seconds=60,
            created_at=None,
            started_at=None,
            ended_at=None,
            updated_at=None,
            provider_call_data={},
            id="exec-100",
        )
        defaults.update(overrides)
        return MagicMock(**defaults)

    @pytest.mark.unit
    def test_get_call_returns_fagi_call_data(self, livekit_service):
        mock_execution = self._mock_call_execution()
        mock_objects = MagicMock()
        mock_objects.get.return_value = mock_execution

        with patch(
            "simulate.models.test_execution.CallExecution.objects",
            mock_objects,
        ):
            result = livekit_service.get_call(MagicMock(call_id="call_exec-100"))

        assert result.call_id == "call_exec-100"
        assert result.duration_seconds == 60
        assert result.cost == 1.5

    @pytest.mark.unit
    def test_get_call_async(self, livekit_service):
        import asyncio

        mock_execution = self._mock_call_execution(
            service_provider_call_id="call_exec-200",
            call_summary=None,
            recording_url=None,
            cost_cents=0,
            duration_seconds=0,
            id="exec-200",
        )
        mock_objects = MagicMock()
        mock_objects.aget = AsyncMock(return_value=mock_execution)

        with patch(
            "simulate.models.test_execution.CallExecution.objects",
            mock_objects,
        ):
            result = asyncio.run(
                livekit_service.get_call_async(MagicMock(call_id="call_exec-200"))
            )

        assert result.call_id == "call_exec-200"


# ============================================================================
# normalize_call_data
# ============================================================================


class TestNormalizeCallData:
    @pytest.mark.unit
    def test_normalizes_raw_dict(self, livekit_service):
        raw = {
            "call_id": "call_exec-300",
            "call_type": "inbound",
            "status": "completed",
            "transcript_available": True,
            "recording_available": True,
            "duration_seconds": 45,
            PROVIDER_KEY: {
                "system_phone_number": "+15551111111",
                "customer_phone_number": "+15552222222",
            },
        }
        result = livekit_service.normalize_call_data(raw, call_data_stored=True)

        assert result.call_id == "call_exec-300"
        assert result.system_phone_number == "+15551111111"
        assert result.customer_phone_number == "+15552222222"
        assert result.duration_seconds == 45

    @pytest.mark.unit
    def test_normalizes_empty_dict(self, livekit_service):
        result = livekit_service.normalize_call_data({}, call_data_stored=False)

        assert result.call_id == ""
        assert result.duration_seconds == 0


# ============================================================================
# iter_call_logs
# ============================================================================


class TestIterCallLogs:
    @pytest.mark.unit
    def test_yields_transcripts_from_db(self, livekit_service):
        from datetime import datetime, timezone

        t1 = MagicMock(
            role="user",
            content="Hello",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        t2 = MagicMock(
            role="assistant",
            content="Hi there",
            created_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )

        with patch("simulate.models.test_execution.CallTranscript.objects") as mock_qs:
            mock_qs.filter.return_value.order_by.return_value = [t1, t2]

            logs = list(livekit_service.iter_call_logs("exec-id-123", verify_ssl=True))

        assert len(logs) == 2
        assert logs[0]["role"] == "user"
        assert logs[0]["content"] == "Hello"
        assert logs[1]["role"] == "assistant"


# ============================================================================
# _run_async
# ============================================================================


class TestRunAsync:
    @pytest.mark.unit
    def test_no_event_loop_uses_asyncio_run(self):
        from ee.voice.services.livekit.service import _run_async

        async def coro():
            return 42

        assert _run_async(coro()) == 42

    @pytest.mark.unit
    def test_with_event_loop_uses_thread_pool(self):
        import asyncio

        from ee.voice.services.livekit.service import _run_async

        async def inner():
            return 99

        async def outer():
            return _run_async(inner())

        result = asyncio.run(outer())
        assert result == 99


# ============================================================================
# Provider key uses enum
# ============================================================================


class TestProviderKey:
    @pytest.mark.unit
    def test_provider_key_matches_enum(self):
        assert PROVIDER_KEY == ProviderChoices.LIVEKIT.value
        assert PROVIDER_KEY == "livekit"


# ============================================================================
# Transcript normalization — recording offset
# ============================================================================


def _make_call_execution(provider_call_data=None, call_metadata=None):
    """Create a mock CallExecution for transcript tests."""
    ce = MagicMock()
    ce.provider_call_data = provider_call_data or {}
    ce.call_metadata = call_metadata or {}
    return ce


class TestTranscriptNormalization:
    """Tests for get_normalized_transcript_data recording offset logic."""

    def _run(self, coro):
        return asyncio.run(coro)

    @pytest.mark.unit
    def test_offset_applied_to_start_and_end(self, livekit_service):
        """recording_offset_ms from provider_call_data shifts both times."""
        ce = _make_call_execution(
            provider_call_data={"livekit": {"recording_offset_ms": 1500}},
        )
        transcripts = [
            {
                "speaker_role": "assistant",
                "content": "Hello",
                "start_time_ms": 0,
                "end_time_ms": 2000,
            },
        ]

        with (
            patch("simulate.models.test_execution.CallExecution.objects") as mock_qs,
            patch("asgiref.sync.sync_to_async") as mock_s2a,
        ):
            mock_qs.aget = AsyncMock(return_value=ce)
            mock_s2a.return_value = AsyncMock(return_value=transcripts)

            result = self._run(livekit_service.get_normalized_transcript_data("call-1"))

        assert len(result.messages) == 1
        msg = result.messages[0]
        # start_time_ms=0 + offset=1500 → 1.5s
        assert msg.time == pytest.approx(1.5)
        # end_time_ms=2000 + offset=1500 → 3.5s
        assert msg.end_time == pytest.approx(3.5)
        assert msg.duration == pytest.approx(2.0)

    @pytest.mark.unit
    def test_offset_zero_when_not_present(self, livekit_service):
        """No offset in provider_call_data → timestamps unchanged."""
        ce = _make_call_execution(
            provider_call_data={"livekit": {}},
        )
        transcripts = [
            {
                "speaker_role": "user",
                "content": "Hi there",
                "start_time_ms": 3000,
                "end_time_ms": 5000,
            },
        ]

        with (
            patch("simulate.models.test_execution.CallExecution.objects") as mock_qs,
            patch("asgiref.sync.sync_to_async") as mock_s2a,
        ):
            mock_qs.aget = AsyncMock(return_value=ce)
            mock_s2a.return_value = AsyncMock(return_value=transcripts)

            result = self._run(livekit_service.get_normalized_transcript_data("call-1"))

        msg = result.messages[0]
        assert msg.time == pytest.approx(3.0)
        assert msg.end_time == pytest.approx(5.0)

    @pytest.mark.unit
    def test_estimated_end_time_uses_offset(self, livekit_service):
        """When end_time_ms is 0 (missing), estimate uses offset start."""
        ce = _make_call_execution(
            provider_call_data={"livekit": {"recording_offset_ms": 2000}},
        )
        transcripts = [
            {
                "speaker_role": "assistant",
                "content": "Hello world",  # 2 words
                "start_time_ms": 1000,
                "end_time_ms": 0,  # missing → estimate
            },
        ]

        with (
            patch("simulate.models.test_execution.CallExecution.objects") as mock_qs,
            patch("asgiref.sync.sync_to_async") as mock_s2a,
        ):
            mock_qs.aget = AsyncMock(return_value=ce)
            mock_s2a.return_value = AsyncMock(return_value=transcripts)

            result = self._run(livekit_service.get_normalized_transcript_data("call-1"))

        msg = result.messages[0]
        # start = (1000 + 2000) / 1000 = 3.0
        assert msg.time == pytest.approx(3.0)
        # end = start + estimated_duration (2 words / 150wpm * 60 = 0.8s)
        assert msg.end_time == pytest.approx(3.8)

    @pytest.mark.unit
    def test_no_provider_call_data(self, livekit_service):
        """Handles None provider_call_data gracefully."""
        ce = _make_call_execution(provider_call_data=None)
        ce.provider_call_data = None
        transcripts = [
            {
                "speaker_role": "user",
                "content": "Test",
                "start_time_ms": 500,
                "end_time_ms": 1500,
            },
        ]

        with (
            patch("simulate.models.test_execution.CallExecution.objects") as mock_qs,
            patch("asgiref.sync.sync_to_async") as mock_s2a,
        ):
            mock_qs.aget = AsyncMock(return_value=ce)
            mock_s2a.return_value = AsyncMock(return_value=transcripts)

            result = self._run(livekit_service.get_normalized_transcript_data("call-1"))

        msg = result.messages[0]
        assert msg.time == pytest.approx(0.5)
        assert msg.end_time == pytest.approx(1.5)

    @pytest.mark.unit
    def test_multiple_transcripts_with_offset(self, livekit_service):
        """Offset applies consistently to all transcript entries."""
        ce = _make_call_execution(
            provider_call_data={"livekit": {"recording_offset_ms": 1000}},
        )
        transcripts = [
            {
                "speaker_role": "assistant",
                "content": "Hello",
                "start_time_ms": 0,
                "end_time_ms": 1000,
            },
            {
                "speaker_role": "user",
                "content": "Hi",
                "start_time_ms": 1500,
                "end_time_ms": 2500,
            },
            {
                "speaker_role": "assistant",
                "content": "How can I help?",
                "start_time_ms": 3000,
                "end_time_ms": 5000,
            },
        ]

        with (
            patch("simulate.models.test_execution.CallExecution.objects") as mock_qs,
            patch("asgiref.sync.sync_to_async") as mock_s2a,
        ):
            mock_qs.aget = AsyncMock(return_value=ce)
            mock_s2a.return_value = AsyncMock(return_value=transcripts)

            result = self._run(livekit_service.get_normalized_transcript_data("call-1"))

        assert len(result.messages) == 3
        # All start/end times shifted by +1s
        assert result.messages[0].time == pytest.approx(1.0)
        assert result.messages[0].end_time == pytest.approx(2.0)
        assert result.messages[1].time == pytest.approx(2.5)
        assert result.messages[1].end_time == pytest.approx(3.5)
        assert result.messages[2].time == pytest.approx(4.0)
        assert result.messages[2].end_time == pytest.approx(6.0)

    @pytest.mark.unit
    def test_token_usage_from_provider_data(self, livekit_service):
        """Token usage is read from the same provider_call_data."""
        ce = _make_call_execution(
            provider_call_data={
                "livekit": {
                    "recording_offset_ms": 0,
                    "usage": {
                        "llm": {
                            "prompt_tokens": 100,
                            "completion_tokens": 50,
                        }
                    },
                }
            },
        )
        transcripts = []

        with (
            patch("simulate.models.test_execution.CallExecution.objects") as mock_qs,
            patch("asgiref.sync.sync_to_async") as mock_s2a,
        ):
            mock_qs.aget = AsyncMock(return_value=ce)
            mock_s2a.return_value = AsyncMock(return_value=transcripts)

            result = self._run(livekit_service.get_normalized_transcript_data("call-1"))

        assert result.token_usage["llm"]["prompt_tokens"] == 100
        assert result.token_usage["llm"]["completion_tokens"] == 50

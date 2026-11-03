"""
Unit tests for LiveKit webhook handler (LiveKitWebhookView).

Tests the webhook endpoint at POST /simulate/api/livekit/webhook/
which processes lifecycle events from the LiveKit server.

Run with: pytest simulate/tests/test_livekit_webhooks.py -v
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from simulate.views.livekit_api import (
    _extract_call_id,
    _handle_egress_ended,
    _handle_room_finished,
    _handle_room_started,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_room_event(event_type: str, room_name: str, creation_time: int = 1700000000):
    """Build a mock LiveKit WebhookEvent."""
    event = MagicMock()
    event.event = event_type
    event.room.name = room_name
    event.room.creation_time = creation_time
    event.participant = None
    event.egress_info = None
    return event


def _make_egress_event(room_name: str, egress_id: str = "eg-123", filenames=None):
    """Build a mock egress_ended event."""
    event = _make_room_event("egress_ended", room_name)
    egress = MagicMock()
    egress.egress_id = egress_id
    egress.room_name = room_name
    file_results = []
    for fn in filenames or ["recording.mp3"]:
        f = MagicMock()
        f.filename = fn
        file_results.append(f)
    egress.file_results = file_results
    event.egress_info = egress
    return event


# ============================================================================
# _extract_call_id
# ============================================================================


class TestExtractCallId:
    @pytest.mark.unit
    def test_valid_uuid(self):
        uid = str(uuid4())
        assert _extract_call_id(f"call_{uid}") == uid

    @pytest.mark.unit
    def test_invalid_prefix(self):
        uid = str(uuid4())
        assert _extract_call_id(f"room_{uid}") is None

    @pytest.mark.unit
    def test_no_uuid(self):
        assert _extract_call_id("call_not-a-uuid") is None

    @pytest.mark.unit
    def test_empty_string(self):
        assert _extract_call_id("") is None


# ============================================================================
# _handle_room_started
# ============================================================================


class TestHandleRoomStarted:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sets_started_at(self):
        call_id = str(uuid4())
        event = _make_room_event("room_started", f"call_{call_id}", 1700000000)

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.update_lifecycle = AsyncMock(return_value=True)
            await _handle_room_started(event)

        mock_repo.update_lifecycle.assert_awaited_once()
        kwargs = mock_repo.update_lifecycle.call_args
        assert kwargs[0][0] == call_id
        assert "started_at" in kwargs[1]
        assert isinstance(kwargs[1]["started_at"], datetime)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_non_call_room(self):
        event = _make_room_event("room_started", "random_room")

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.update_lifecycle = AsyncMock()
            mock_repo.get_call_id_by_room_name = AsyncMock(return_value=None)
            await _handle_room_started(event)

        mock_repo.update_lifecycle.assert_not_awaited()


# ============================================================================
# _handle_room_finished
# ============================================================================


class TestHandleRoomFinished:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_updates_lifecycle_and_signals_temporal(self):
        call_id = str(uuid4())
        event = _make_room_event("room_finished", f"call_{call_id}", 1700000000)

        config = {
            "provider_call_data": {"livekit": {"workflow_id": "wf-123"}},
            "ended_reason": "agent_ended_call",
        }
        with (
            patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo,
            patch("simulate.views.livekit_api._signal_temporal") as mock_signal,
        ):
            mock_repo.get_call_config = AsyncMock(return_value=config)
            mock_repo.update_lifecycle = AsyncMock(return_value=True)
            mock_signal.return_value = None
            mock_signal = AsyncMock()
            with patch("simulate.views.livekit_api._signal_temporal", mock_signal):
                await _handle_room_finished(event)

        mock_repo.update_lifecycle.assert_awaited_once()
        kwargs = mock_repo.update_lifecycle.call_args[1]
        assert "completed_at" in kwargs
        assert "ended_at" in kwargs
        assert "duration_seconds" in kwargs
        assert kwargs["ended_reason"] == "agent_ended_call"
        mock_signal.assert_awaited_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_defaults_ended_reason_to_call_completed(self):
        call_id = str(uuid4())
        event = _make_room_event("room_finished", f"call_{call_id}", 1700000000)

        config = {
            "provider_call_data": {"livekit": {}},
            "ended_reason": "",
        }
        with (
            patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo,
            patch(
                "simulate.views.livekit_api._signal_temporal", new_callable=AsyncMock
            ),
        ):
            mock_repo.get_call_config = AsyncMock(return_value=config)
            mock_repo.update_lifecycle = AsyncMock(return_value=True)
            await _handle_room_finished(event)

        kwargs = mock_repo.update_lifecycle.call_args[1]
        assert kwargs["ended_reason"] == "call_completed"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_when_call_not_found(self):
        call_id = str(uuid4())
        event = _make_room_event("room_finished", f"call_{call_id}")

        with (
            patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo,
            patch(
                "simulate.views.livekit_api._signal_temporal", new_callable=AsyncMock
            ) as mock_signal,
        ):
            mock_repo.get_call_config = AsyncMock(return_value=None)
            mock_repo.update_lifecycle = AsyncMock()
            await _handle_room_finished(event)

        mock_repo.update_lifecycle.assert_not_awaited()
        mock_signal.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_non_call_room(self):
        event = _make_room_event("room_finished", "test_room")

        with (
            patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo,
            patch(
                "simulate.views.livekit_api._signal_temporal", new_callable=AsyncMock
            ) as mock_signal,
        ):
            mock_repo.get_call_config = AsyncMock()
            mock_repo.get_call_id_by_room_name = AsyncMock(return_value=None)
            await _handle_room_finished(event)

        mock_repo.get_call_config.assert_not_awaited()
        mock_signal.assert_not_awaited()


# ============================================================================
# _handle_egress_ended
# ============================================================================


class TestHandleEgressEnded:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stores_recording_files(self):
        call_id = str(uuid4())
        event = _make_egress_event(
            f"call_{call_id}",
            egress_id="eg-456",
            filenames=["stereo.mp3", "agent.mp3"],
        )

        config = {"provider_call_data": {"livekit": {"room_name": f"call_{call_id}"}}}
        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock(return_value=config)
            mock_repo.update_lifecycle = AsyncMock(return_value=True)
            await _handle_egress_ended(event)

        mock_repo.update_lifecycle.assert_awaited_once()
        kwargs = mock_repo.update_lifecycle.call_args[1]
        pcd = kwargs["provider_call_data"]
        assert pcd["livekit"]["recording_files"] == ["stereo.mp3", "agent.mp3"]
        assert pcd["livekit"]["egress_id"] == "eg-456"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_when_no_egress_info(self):
        event = MagicMock()
        event.egress_info = None

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock()
            await _handle_egress_ended(event)

        mock_repo.get_call_config.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_non_call_room(self):
        event = _make_egress_event("random_room")

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock()
            mock_repo.get_call_id_by_room_name = AsyncMock(return_value=None)
            await _handle_egress_ended(event)

        mock_repo.get_call_config.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_when_call_not_found(self):
        call_id = str(uuid4())
        event = _make_egress_event(f"call_{call_id}")

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock(return_value=None)
            mock_repo.update_lifecycle = AsyncMock()
            await _handle_egress_ended(event)

        mock_repo.update_lifecycle.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_preserves_existing_provider_data(self):
        """Egress data should merge into existing provider_call_data, not overwrite."""
        call_id = str(uuid4())
        event = _make_egress_event(f"call_{call_id}", filenames=["new.mp3"])

        config = {
            "provider_call_data": {
                "livekit": {
                    "room_name": f"call_{call_id}",
                    "dispatch_id": "d-existing",
                }
            }
        }
        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock(return_value=config)
            mock_repo.update_lifecycle = AsyncMock(return_value=True)
            await _handle_egress_ended(event)

        kwargs = mock_repo.update_lifecycle.call_args[1]
        lk = kwargs["provider_call_data"]["livekit"]
        # Old key preserved
        assert lk["dispatch_id"] == "d-existing"
        # New keys added
        assert lk["recording_files"] == ["new.mp3"]

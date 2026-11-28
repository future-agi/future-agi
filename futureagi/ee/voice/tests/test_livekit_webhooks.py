"""
Tests for the LiveKit webhook handler (LiveKitWebhookView).

Tests the webhook endpoint at POST /simulate/api/livekit/webhook/
which processes lifecycle events from the LiveKit server.

Run with: pytest ee/voice/tests/test_livekit_webhooks.py -v
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.db import connection

from accounts.models import Organization
from simulate.models import CallExecution, RunTest, Scenarios
from simulate.models import TestExecution as SimulationExecution
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


def _make_recording_call(organization, provider_data):
    run = RunTest.objects.create(name="Recording", organization=organization)
    scenario = Scenarios.objects.create(
        name="Recording", source="webhook", organization=organization
    )
    return CallExecution.objects.create(
        test_execution=SimulationExecution.objects.create(run_test=run),
        scenario=scenario,
        provider_call_data=provider_data,
    )


class TestHandleEgressEnded:
    @pytest.mark.django_db
    @pytest.mark.parametrize("initial", [None, {"vapi": {"id": "existing"}}])
    def test_stores_recording_files(self, organization, initial):
        target = _make_recording_call(organization, initial)
        event = _make_egress_event(
            f"call_{target.pk}",
            egress_id="eg-456",
            filenames=["stereo.mp3", "", "agent.mp3"],
        )
        async_to_sync(_handle_egress_ended)(event)

        target.refresh_from_db()
        assert target.provider_call_data == {
            **(initial or {}),
            "livekit": {
                "egress_id": "eg-456",
                "recording_files": ["stereo.mp3", "agent.mp3"],
            },
        }

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_when_no_egress_info(self):
        event = MagicMock()
        event.egress_info = None

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            await _handle_egress_ended(event)

        assert not mock_repo.mock_calls

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_skips_non_call_room(self):
        event = _make_egress_event("random_room")

        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_id_by_room_name = AsyncMock(return_value=None)
            await _handle_egress_ended(event)

        assert mock_repo.mock_calls == [call.get_call_id_by_room_name("random_room")]

    @pytest.mark.django_db
    def test_skips_when_call_not_found(self):
        call_id = uuid4()
        event = _make_egress_event(f"call_{call_id}")
        with patch("simulate.views.livekit_api.logger") as logger:
            async_to_sync(_handle_egress_ended)(event)

        assert not CallExecution.objects.filter(pk=call_id).exists()
        logger.info.assert_not_called()

    @pytest.mark.django_db(transaction=True)
    def test_completed_split_survives_egress_write(self, organization):
        initial = {
            "livekit": {
                "egress_id": "eg-123",
                "recording_files": ["prior.ogg"],
                "recording": {"stereo": "partial.mp3"},
                "recording_error": {"tracks": ["assistant"]},
            },
            "vapi": {"id": "before"},
        }
        target = _make_recording_call(organization, initial)
        other = _make_recording_call(Organization.objects.create(name="Other"), initial)
        source = {"call_id": str(target.pk), "egress_id": "eg-123"}
        tracks = {
            k: f"call-recordings/{target.pk}/{k}.mp3"
            for k in ("stereo", "assistant", "customer", "combined")
        }
        completed = {
            "livekit": {
                "egress_id": source["egress_id"],
                "recording_files": ["prior.ogg"],
                "recording": tracks,
                "recording_source": source,
                "recording_cleanup_success": {**source, "recording": tracks},
            },
            "vapi": {"id": "after"},
        }
        event = _make_egress_event(f"call_{target.pk}", filenames=["raw.ogg"])
        update_call = f'UPDATE "{CallExecution._meta.db_table}"'
        split_committed = False

        def finish_split_before_write(execute, sql, params, many, context):
            nonlocal split_committed
            if not split_committed and sql.startswith(update_call):
                # Commit the split/cleanup result after any webhook reads but before
                # its UPDATE executes. Neither writer nor its ORM result is mocked.
                split_committed = True
                CallExecution.objects.filter(pk=target.pk).update(
                    provider_call_data=completed,
                    status=CallExecution.CallStatus.COMPLETED,
                )
            return execute(sql, params, many, context)

        with connection.execute_wrapper(finish_split_before_write):
            async_to_sync(_handle_egress_ended)(event)

        target.refresh_from_db()
        other.refresh_from_db()
        assert split_committed
        assert target.provider_call_data == {
            **completed,
            "livekit": {**completed["livekit"], "recording_files": ["raw.ogg"]},
        }
        assert target.status == CallExecution.CallStatus.COMPLETED
        assert other.provider_call_data == initial

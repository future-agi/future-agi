"""
Unit tests for LiveKit internal API endpoints.

Tests the agent-worker → backend API endpoints:
  - GET  /simulate/api/livekit/call-config/<call_id>/
  - POST /simulate/api/livekit/transcripts/<call_id>/
  - GET  /simulate/api/livekit/phone-resolution/<phone_number>/
  - PATCH /simulate/api/livekit/call-execution/<call_id>/
  - POST /simulate/api/livekit/temporal-signal/

Run with: pytest simulate/tests/test_livekit_api.py -v
"""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from django.test import RequestFactory, override_settings

INTERNAL_SECRET = "test-secret-key-for-testing"


# ============================================================================
# Helpers
# ============================================================================


def _make_request(method, path, data=None, auth_token=None):
    """Create a Django request via RequestFactory (bypasses middleware)."""
    factory = RequestFactory()
    kwargs = {}
    if data is not None:
        kwargs["data"] = data
        kwargs["content_type"] = "application/json"
    request = getattr(factory, method)(path, **kwargs)
    if auth_token:
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {auth_token}"
    return request


def _dispatch(view_class, request, **url_kwargs):
    """Dispatch request to an async view and return the response."""
    view = view_class.as_view()
    response = asyncio.run(view(request, **url_kwargs))
    if hasattr(response, "render"):
        response.render()
    return response


# ============================================================================
# Auth
# ============================================================================


class TestInternalAuth:
    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_missing_auth_returns_403(self):
        from simulate.views.livekit_api import CallConfigView

        call_id = str(uuid4())
        request = _make_request("get", f"/simulate/api/livekit/call-config/{call_id}/")
        response = _dispatch(CallConfigView, request, call_id=call_id)
        assert response.status_code == 401

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_invalid_token_returns_403(self):
        from simulate.views.livekit_api import CallConfigView

        call_id = str(uuid4())
        request = _make_request(
            "get",
            f"/simulate/api/livekit/call-config/{call_id}/",
            auth_token="wrong-token",
        )
        response = _dispatch(CallConfigView, request, call_id=call_id)
        assert response.status_code == 401

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_non_bearer_auth_returns_403(self):
        from simulate.views.livekit_api import CallConfigView

        call_id = str(uuid4())
        factory = RequestFactory()
        request = factory.get(f"/simulate/api/livekit/call-config/{call_id}/")
        request.META["HTTP_AUTHORIZATION"] = f"Token {INTERNAL_SECRET}"
        response = _dispatch(CallConfigView, request, call_id=call_id)
        assert response.status_code == 401


# ============================================================================
# GET /simulate/api/livekit/call-config/<call_id>/
# ============================================================================


class TestCallConfigView:
    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_returns_config(self):
        from simulate.views.livekit_api import CallConfigView

        call_id = str(uuid4())
        config = {
            "id": call_id,
            "call_metadata": {"system_prompt": "Be helpful."},
            "provider_call_data": {},
            "status": "ongoing",
            "ended_reason": "",
        }
        request = _make_request(
            "get",
            f"/simulate/api/livekit/call-config/{call_id}/",
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock(return_value=config)
            response = _dispatch(CallConfigView, request, call_id=call_id)

        assert response.status_code == 200
        assert response.data["id"] == call_id
        assert response.data["call_metadata"]["system_prompt"] == "Be helpful."

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_returns_404_when_not_found(self):
        from simulate.views.livekit_api import CallConfigView

        call_id = str(uuid4())
        request = _make_request(
            "get",
            f"/simulate/api/livekit/call-config/{call_id}/",
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.get_call_config = AsyncMock(return_value=None)
            response = _dispatch(CallConfigView, request, call_id=call_id)

        assert response.status_code == 404


# ============================================================================
# POST /simulate/api/livekit/transcripts/<call_id>/
# ============================================================================


class TestTranscriptsView:
    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_single_transcript(self):
        from simulate.views.livekit_api import TranscriptsView

        call_id = str(uuid4())
        payload = {
            "role": "agent",
            "content": "Hello, how can I help?",
            "start_time_ms": 1000,
            "end_time_ms": 3000,
        }
        request = _make_request(
            "post",
            f"/simulate/api/livekit/transcripts/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallTranscriptRepository") as mock_repo:
            mock_repo.create = AsyncMock(
                return_value={"id": 1, "role": "agent", "content": payload["content"]}
            )
            response = _dispatch(TranscriptsView, request, call_id=call_id)

        assert response.status_code == 201
        mock_repo.create.assert_awaited_once()

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_bulk_transcripts(self):
        from simulate.views.livekit_api import TranscriptsView

        call_id = str(uuid4())
        payload = {
            "transcripts": [
                {"role": "agent", "content": "Hello", "start_time_ms": 0},
                {"role": "user", "content": "Hi there", "start_time_ms": 2000},
            ]
        }
        request = _make_request(
            "post",
            f"/simulate/api/livekit/transcripts/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallTranscriptRepository") as mock_repo:
            mock_repo.bulk_create = AsyncMock(return_value=2)
            response = _dispatch(TranscriptsView, request, call_id=call_id)

        assert response.status_code == 201
        assert response.data["created"] == 2

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_missing_required_field(self):
        from simulate.views.livekit_api import TranscriptsView

        call_id = str(uuid4())
        payload = {"role": "agent", "content": "Hello"}  # missing start_time_ms
        request = _make_request(
            "post",
            f"/simulate/api/livekit/transcripts/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        response = _dispatch(TranscriptsView, request, call_id=call_id)
        assert response.status_code == 400
        assert "start_time_ms" in response.data["error"]

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_empty_bulk_list_returns_400(self):
        from simulate.views.livekit_api import TranscriptsView

        call_id = str(uuid4())
        payload = {"transcripts": []}
        request = _make_request(
            "post",
            f"/simulate/api/livekit/transcripts/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        response = _dispatch(TranscriptsView, request, call_id=call_id)
        assert response.status_code == 400

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_single_transcript_not_found_returns_404(self):
        from simulate.views.livekit_api import TranscriptsView

        call_id = str(uuid4())
        payload = {"role": "agent", "content": "Hello", "start_time_ms": 0}
        request = _make_request(
            "post",
            f"/simulate/api/livekit/transcripts/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallTranscriptRepository") as mock_repo:
            mock_repo.create = AsyncMock(
                side_effect=Exception("CallExecution not found")
            )
            response = _dispatch(TranscriptsView, request, call_id=call_id)
        assert response.status_code == 404


# ============================================================================
# GET /simulate/api/livekit/phone-resolution/<phone_number>/
# ============================================================================


class TestPhoneResolutionView:
    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_resolves_phone(self):
        from simulate.views.livekit_api import PhoneResolutionView

        config = {
            "call_id": str(uuid4()),
            "system_prompt": "You are a support agent.",
            "voice_settings": {"language": "en"},
        }
        request = _make_request(
            "get",
            "/simulate/api/livekit/phone-resolution/+15551234567/",
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.PhoneNumberRepository") as mock_repo:
            mock_repo.resolve_to_call_config = AsyncMock(return_value=config)
            response = _dispatch(
                PhoneResolutionView, request, phone_number="+15551234567"
            )

        assert response.status_code == 200
        assert response.data["system_prompt"] == "You are a support agent."

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_returns_404_when_not_found(self):
        from simulate.views.livekit_api import PhoneResolutionView

        request = _make_request(
            "get",
            "/simulate/api/livekit/phone-resolution/+15559999999/",
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.PhoneNumberRepository") as mock_repo:
            mock_repo.resolve_to_call_config = AsyncMock(return_value=None)
            response = _dispatch(
                PhoneResolutionView, request, phone_number="+15559999999"
            )

        assert response.status_code == 404

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_returns_full_outbound_config(self):
        """Returns outbound call config with all SIP-relevant fields."""
        from simulate.views.livekit_api import PhoneResolutionView

        config = {
            "call_id": str(uuid4()),
            "call_metadata": {"workflow_id": "call-exec-abc"},
            "provider_call_data": {
                "livekit": {
                    "system_prompt": "You are testing an outbound agent.",
                    "voice_settings": {"language": "en", "tts_model": "sonic-2"},
                    "outbound_sip": True,
                    "workflow_id": "call-exec-abc",
                }
            },
            "status": "pending",
        }
        request = _make_request(
            "get",
            "/simulate/api/livekit/phone-resolution/+12175696753/",
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.PhoneNumberRepository") as mock_repo:
            mock_repo.resolve_to_call_config = AsyncMock(return_value=config)
            response = _dispatch(
                PhoneResolutionView, request, phone_number="+12175696753"
            )

        assert response.status_code == 200
        lk_data = response.data["provider_call_data"]["livekit"]
        assert lk_data["outbound_sip"] is True
        assert lk_data["system_prompt"] == "You are testing an outbound agent."
        assert lk_data["workflow_id"] == "call-exec-abc"

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_unauthorized_without_secret(self):
        """Returns 403 without proper auth token."""
        from simulate.views.livekit_api import PhoneResolutionView

        request = _make_request(
            "get",
            "/simulate/api/livekit/phone-resolution/+15551234567/",
            auth_token="wrong-secret",
        )
        with patch("simulate.views.livekit_api.PhoneNumberRepository") as mock_repo:
            mock_repo.resolve_to_call_config = AsyncMock(return_value={"call_id": "x"})
            response = _dispatch(
                PhoneResolutionView, request, phone_number="+15551234567"
            )

        assert response.status_code == 401


# ============================================================================
# PATCH /simulate/api/livekit/call-execution/<call_id>/
# ============================================================================


class TestCallExecutionUpdateView:
    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_updates_allowed_fields(self):
        from simulate.views.livekit_api import CallExecutionUpdateView

        call_id = str(uuid4())
        payload = {
            "duration_seconds": 120,
            "ended_reason": "agent_ended_call",
        }
        request = _make_request(
            "patch",
            f"/simulate/api/livekit/call-execution/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.update_lifecycle = AsyncMock(return_value=True)
            response = _dispatch(CallExecutionUpdateView, request, call_id=call_id)

        assert response.status_code == 200
        assert response.data["ok"] is True
        mock_repo.update_lifecycle.assert_awaited_once()

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_rejects_disallowed_fields(self):
        from simulate.views.livekit_api import CallExecutionUpdateView

        call_id = str(uuid4())
        # Serializer strict-input rejects these before allowed_fields runs.
        payload = {"status": "completed", "id": "fake-id"}
        request = _make_request(
            "patch",
            f"/simulate/api/livekit/call-execution/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        response = _dispatch(CallExecutionUpdateView, request, call_id=call_id)
        assert response.status_code == 400
        assert "Unknown field" in response.data["error"]

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_returns_404_when_not_found(self):
        from simulate.views.livekit_api import CallExecutionUpdateView

        call_id = str(uuid4())
        payload = {"duration_seconds": 60}
        request = _make_request(
            "patch",
            f"/simulate/api/livekit/call-execution/{call_id}/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch("simulate.views.livekit_api.CallExecutionRepository") as mock_repo:
            mock_repo.update_lifecycle = AsyncMock(return_value=False)
            response = _dispatch(CallExecutionUpdateView, request, call_id=call_id)
        assert response.status_code == 404


# ============================================================================
# POST /simulate/api/livekit/temporal-signal/
# ============================================================================


class TestTemporalSignalView:
    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_sends_signal_with_workflow_id(self):
        from unittest.mock import MagicMock

        from simulate.views.livekit_api import TemporalSignalView

        payload = {
            "workflow_id": "wf-abc-123",
            "call_id": str(uuid4()),
            "status": "completed",
            "duration_seconds": 60,
            "end_reason": "agent_ended_call",
        }
        mock_handle = AsyncMock()
        mock_client = MagicMock()
        mock_client.get_workflow_handle.return_value = mock_handle

        request = _make_request(
            "post",
            "/simulate/api/livekit/temporal-signal/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch(
            "tfc.temporal.get_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            response = _dispatch(TemporalSignalView, request)

        assert response.status_code == 200
        assert response.data["ok"] is True
        mock_handle.signal.assert_awaited_once()

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_requires_workflow_id_or_call_id(self):
        from simulate.views.livekit_api import TemporalSignalView

        payload = {"status": "completed"}  # no workflow_id, no call_id
        request = _make_request(
            "post",
            "/simulate/api/livekit/temporal-signal/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        response = _dispatch(TemporalSignalView, request)
        assert response.status_code == 400

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_falls_back_to_call_id(self):
        """When workflow_id is missing, constructs it from call_id."""
        from unittest.mock import MagicMock

        from simulate.views.livekit_api import TemporalSignalView

        call_id = str(uuid4())
        payload = {"call_id": call_id, "status": "completed"}

        mock_handle = AsyncMock()
        mock_client = MagicMock()
        mock_client.get_workflow_handle.return_value = mock_handle

        request = _make_request(
            "post",
            "/simulate/api/livekit/temporal-signal/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch(
            "tfc.temporal.get_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            response = _dispatch(TemporalSignalView, request)

        assert response.status_code == 200
        # Verify the constructed workflow_id includes the call_id
        wf_id = mock_client.get_workflow_handle.call_args[1]["workflow_id"]
        assert call_id in wf_id

    @pytest.mark.unit
    @override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
    def test_temporal_error_returns_502(self):
        from simulate.views.livekit_api import TemporalSignalView

        payload = {"workflow_id": "wf-bad", "status": "completed"}
        request = _make_request(
            "post",
            "/simulate/api/livekit/temporal-signal/",
            data=payload,
            auth_token=INTERNAL_SECRET,
        )
        with patch(
            "tfc.temporal.get_client",
            new_callable=AsyncMock,
            side_effect=Exception("Temporal unavailable"),
        ):
            response = _dispatch(TemporalSignalView, request)

        assert response.status_code == 502

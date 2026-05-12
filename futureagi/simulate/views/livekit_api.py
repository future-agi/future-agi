"""LiveKit agent worker internal API and webhook handler.

Two authentication modes:
1. **Internal API** (agent worker → backend): Shared secret via
   ``Authorization: Bearer <INTERNAL_API_SECRET>``.
2. **Webhook** (LiveKit server → backend): JWT-signed by LiveKit using
   API key/secret, verified via ``livekit.api.WebhookReceiver``.
"""

from __future__ import annotations

import inspect
import re
import uuid
from datetime import UTC, datetime

import structlog
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.repositories import (
    CallExecutionRepository,
    CallTranscriptRepository,
    PhoneNumberRepository,
)
from tfc.utils.general_methods import GeneralMethods
from tracer.models.observability_provider import ProviderChoices

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Async-aware DRF APIView base
# ---------------------------------------------------------------------------


class AsyncAPIView(APIView):
    """APIView subclass that correctly awaits async handler methods.

    DRF's ``APIView.dispatch()`` does not await async handlers, causing
    them to return coroutine objects instead of Response instances.
    This override detects async handlers and awaits them.
    """

    async def dispatch(self, request, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        try:
            self.initial(request, *args, **kwargs)

            if request.method.lower() in self.http_method_names:
                handler = getattr(
                    self, request.method.lower(), self.http_method_not_allowed
                )
            else:
                handler = self.http_method_not_allowed

            response = handler(request, *args, **kwargs)
            if inspect.isawaitable(response):
                response = await response

        except Exception as exc:
            response = self.handle_exception(exc)

        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class InternalAPIView(AsyncAPIView):
    """Base view that authenticates via shared secret.

    Skips DRF's default authentication/permission classes — the agent
    worker is a service, not a user.
    """

    authentication_classes = []
    permission_classes = []

    def initial(self, request: Request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        secret = settings.INTERNAL_API_SECRET
        if not secret:
            raise _forbidden("INTERNAL_API_SECRET not configured")

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            raise _forbidden("Missing Bearer token")

        token = auth_header[7:]
        if token != secret:
            raise _forbidden("Invalid token")


def _forbidden(detail: str):
    from rest_framework.exceptions import AuthenticationFailed

    return AuthenticationFailed(detail)


# ---------------------------------------------------------------------------
# GET /simulate/api/livekit/call-config/<call_id>/
# ---------------------------------------------------------------------------


class CallConfigView(InternalAPIView):
    """Return call config for a given call execution."""

    async def get(self, request: Request, call_id: str) -> Response:
        config = await CallExecutionRepository.get_call_config(call_id)
        if config is None:
            return Response(
                {"error": "CallExecution not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(config)


# ---------------------------------------------------------------------------
# POST /simulate/api/livekit/transcripts/<call_id>/
# ---------------------------------------------------------------------------


class TranscriptsView(InternalAPIView):
    """Create transcript row(s) for a call execution."""

    async def post(self, request: Request, call_id: str) -> Response:
        data = request.data

        # Bulk mode: {"transcripts": [{role, content, start_time_ms}, ...]}
        if "transcripts" in data:
            transcripts = data["transcripts"]
            if not isinstance(transcripts, list) or not transcripts:
                return Response(
                    {"error": "transcripts must be a non-empty list"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                count = await CallTranscriptRepository.bulk_create(call_id, transcripts)
            except Exception:
                logger.exception("livekit_api_bulk_transcript_failed", call_id=call_id)
                return Response(
                    {"error": "CallExecution not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            return Response({"created": count}, status=status.HTTP_201_CREATED)

        # Single mode: {role, content, start_time_ms}
        for field in ("role", "content", "start_time_ms"):
            if field not in data:
                return Response(
                    {"error": f"Missing required field: {field}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            result = await CallTranscriptRepository.create(
                call_id=call_id,
                role=data["role"],
                content=data["content"],
                start_time_ms=data["start_time_ms"],
                end_time_ms=data.get("end_time_ms", 0),
            )
        except Exception:
            logger.exception("livekit_api_transcript_failed", call_id=call_id)
            return Response(
                {"error": "CallExecution not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(result, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /simulate/api/livekit/phone-resolution/<phone_number>/
# ---------------------------------------------------------------------------


class PhoneResolutionView(InternalAPIView):
    """Resolve a phone number to its linked call config."""

    async def get(self, request: Request, phone_number: str) -> Response:
        config = await PhoneNumberRepository.resolve_to_call_config(phone_number)
        if config is None:
            return Response(
                {"error": "No IN_USE phone number found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(config)


# ---------------------------------------------------------------------------
# PATCH /simulate/api/livekit/call-execution/<call_id>/
# ---------------------------------------------------------------------------


class CallExecutionUpdateView(InternalAPIView):
    """Update lifecycle fields on a call execution."""

    async def patch(self, request: Request, call_id: str) -> Response:
        data = request.data
        allowed_fields = {
            "provider_call_data",
            "started_at",
            "completed_at",
            "ended_at",
            "duration_seconds",
            "ended_reason",
            "service_provider_call_id",
        }
        kwargs = {k: v for k, v in data.items() if k in allowed_fields}
        if not kwargs:
            return Response(
                {"error": "No valid fields provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = await CallExecutionRepository.update_lifecycle(call_id, **kwargs)
        if not updated:
            return Response(
                {"error": "CallExecution not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"ok": True})


# ---------------------------------------------------------------------------
# POST /simulate/api/livekit/temporal-signal/
# ---------------------------------------------------------------------------


class TemporalSignalView(InternalAPIView):
    """Send a call_ended signal to a Temporal workflow."""

    async def post(self, request: Request) -> Response:
        data = request.data
        workflow_id = data.get("workflow_id", "")
        call_id = data.get("call_id", "")
        signal_status = data.get("status", "completed")
        duration_seconds = data.get("duration_seconds", 0)
        end_reason = data.get("end_reason", "agent_session_closed")

        if not workflow_id:
            # Construct from call_id as fallback
            from simulate.temporal.constants import (
                CALL_EXECUTION_WORKFLOW_ID_PREFIX,
            )

            if not call_id:
                return Response(
                    {"error": "workflow_id or call_id required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            workflow_id = f"{CALL_EXECUTION_WORKFLOW_ID_PREFIX}-{call_id}"
            logger.warning(
                "livekit_api_signal_fallback_workflow_id",
                call_id=call_id,
                workflow_id=workflow_id,
            )

        try:
            from tfc.temporal import get_client

            client = await get_client()
            handle = client.get_workflow_handle(workflow_id=workflow_id)
            await handle.signal(
                "call_ended",
                args=[signal_status, duration_seconds, end_reason],
            )
            logger.info(
                "livekit_api_temporal_signal_sent",
                call_id=call_id,
                workflow_id=workflow_id,
            )
            return Response({"ok": True})
        except Exception as e:
            logger.exception(
                "livekit_api_temporal_signal_failed",
                call_id=call_id,
                workflow_id=workflow_id,
            )
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )


# ---------------------------------------------------------------------------
# POST /simulate/api/livekit/webhook/
# ---------------------------------------------------------------------------

# Room name pattern: "call_{uuid}"
_ROOM_NAME_RE = re.compile(r"^call_([0-9a-f-]{36})$")


class LiveCallListenerTokenView(APIView):
    """Generate a read-only LiveKit token for live call monitoring.

    GET /simulate/api/livekit/listener-token/<call_id>/

    Returns a JWT that allows the user to join the call's LiveKit room
    as a listener (can subscribe to audio, cannot publish). Used by the
    frontend to let users hear an ongoing call in real-time.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request: Request, call_id: str) -> Response:
        from livekit.api import AccessToken, VideoGrants

        from simulate.models.test_execution import CallExecution

        try:
            call = CallExecution.objects.select_related(
                "test_execution__run_test__agent_definition"
            ).get(id=call_id)
        except CallExecution.DoesNotExist:
            return self.gm.not_found("Call not found")

        # Verify the requesting user belongs to the same organization
        user_organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        call_organization = call.test_execution.run_test.agent_definition.organization
        if not user_organization or user_organization != call_organization:
            return self.gm.not_found("Not found")

        # Check call is ongoing
        if call.status != CallExecution.CallStatus.ONGOING:
            return self.gm.bad_request(f"Call is not ongoing (status: {call.status})")

        # Get room name from provider data
        provider_data = call.provider_call_data or {}
        lk_data = provider_data.get(ProviderChoices.LIVEKIT, {})
        room_name = lk_data.get("room_name") or f"call_{call_id}"

        # Generate listener token (subscribe only, no publish)
        try:
            token = AccessToken(
                settings.LIVEKIT_API_KEY,
                settings.LIVEKIT_API_SECRET,
            )
            token.with_identity(f"listener-{request.user.id}")
            token.with_name(f"{request.user.email} (listener)")
            token.with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_subscribe=True,
                    can_publish=False,
                    can_publish_data=False,
                )
            )

            return self.gm.success_response(
                {
                    "token": token.to_jwt(),
                    "url": settings.LIVEKIT_URL,
                    "room_name": room_name,
                }
            )
        except Exception:
            logger.exception("listener_token_generation_failed", call_id=call_id)
            return self.gm.internal_server_error_response(
                "Failed to generate listener token. Please try again."
            )


class ValidateLiveKitCredentialsView(APIView):
    """Validate customer-provided LiveKit credentials.

    POST /simulate/api/livekit/validate-credentials/

    Creates a temporary room on the customer's LiveKit server using their
    credentials and immediately deletes it. Returns whether the credentials
    are valid.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request: Request) -> Response:
        from livekit.api import (
            CreateAgentDispatchRequest,
            CreateRoomRequest,
            DeleteRoomRequest,
            LiveKitAPI,
        )

        from simulate.serializers.agent_definition import _is_masked
        from ee.voice.services.livekit.service import _run_async

        data = request.data
        livekit_url = (data.get("livekit_url") or "").strip()
        api_key = (data.get("api_key") or "").strip()
        api_secret = (data.get("api_secret") or "").strip()
        agent_name = (data.get("agent_name") or "").strip()
        agent_definition_id = (data.get("agent_definition_id") or "").strip()

        # When validating an existing agent from the edit form, the api_key
        # and api_secret fields contain MASKED display values (the api_key
        # mask is "abcd...wxyz" / 11 chars from mask_key(); the secret mask
        # is "********"). Sending those verbatim to LiveKit Cloud yields a
        # 401 because they aren't real credentials. Rehydrate any masked
        # field from the stored ProviderCredentials before validating. The
        # url is plaintext on read so it doesn't need this branch.
        if agent_definition_id and (_is_masked(api_key) or _is_masked(api_secret)):
            try:
                from simulate.models.agent_definition import ProviderCredentials

                creds = ProviderCredentials.objects.get(
                    agent_definition_id=agent_definition_id
                )
                if _is_masked(api_key):
                    api_key = creds.get_api_key()
                if _is_masked(api_secret):
                    api_secret = creds.get_api_secret()
            except ProviderCredentials.DoesNotExist:
                pass

        if not all([livekit_url, api_key, api_secret]):
            return self.gm.bad_request("All credential fields are required.")

        # Convert wss:// to https:// and ws:// to http:// for the API URL
        http_url = livekit_url.replace("wss://", "https://").replace("ws://", "http://")

        async def _validate():
            room_name = f"_validate_creds_{uuid.uuid4().hex[:8]}"
            lkapi = LiveKitAPI(
                url=http_url,
                api_key=api_key,
                api_secret=api_secret,
            )
            try:
                await lkapi.room.create_room(
                    CreateRoomRequest(name=room_name, empty_timeout=5)
                )
                # If agent name provided, verify it can be dispatched
                if agent_name:
                    try:
                        await lkapi.agent_dispatch.create_dispatch(
                            CreateAgentDispatchRequest(
                                agent_name=agent_name,
                                room=room_name,
                            )
                        )
                    except Exception as dispatch_err:
                        err_str = str(dispatch_err).lower()
                        if (
                            "not found" in err_str
                            or "no agent" in err_str
                            or "unavailable" in err_str
                        ):
                            raise RuntimeError(
                                f"agent_not_found:No agent worker with name '{agent_name}' "
                                f"is registered on {livekit_url}"
                            ) from dispatch_err
                        # Other dispatch errors — agent might still be valid,
                        # just not available right now
            finally:
                # Always delete the room to kill any dispatched agent session
                try:
                    await lkapi.room.delete_room(DeleteRoomRequest(room=room_name))
                except Exception:
                    pass  # Room may not exist if create_room failed
                await lkapi.aclose()

        try:
            _run_async(_validate())
        except Exception as exc:
            logger.warning(
                "livekit_validate_credentials_failed",
                error=str(exc),
                url=livekit_url,
            )
            return self.gm.success_response({"valid": False, "error": str(exc)})

        return self.gm.success_response({"valid": True})


class LiveKitWebhookView(AsyncAPIView):
    """Receive and process LiveKit server webhooks.

    Handles lifecycle events (room start/finish, participant join/leave,
    egress completion). This is the **reliable** path for updating call
    lifecycle and signaling Temporal — it fires even if the agent worker
    crashes mid-call.

    Auth: JWT-signed by LiveKit server, verified with API key/secret.
    """

    authentication_classes = []
    permission_classes = []

    async def post(self, request: Request) -> Response:
        # Verify webhook signature
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        logger.info(
            "livekit_webhook_debug",
            auth_header=auth_header[:80] if auth_header else "",
            body_len=len(request.body) if hasattr(request, "_request") else -1,
        )
        # LiveKit server sends Authorization: <jwt> (no "Bearer " prefix)
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif auth_header:
            token = auth_header
        else:
            return Response(
                {"error": "Missing Authorization header"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            from livekit.api import TokenVerifier, WebhookReceiver

            verifier = TokenVerifier(
                api_key=settings.LIVEKIT_API_KEY,
                api_secret=settings.LIVEKIT_API_SECRET,
            )
            receiver = WebhookReceiver(verifier)
            event = receiver.receive(
                body=request.body.decode("utf-8"),
                auth_token=token,
            )
        except Exception as exc:
            logger.exception(
                "livekit_webhook_verification_failed",
                error_type=type(exc).__name__,
                error_msg=str(exc),
            )
            return Response(
                {"error": f"Webhook verification failed: {type(exc).__name__}: {exc}"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        event_type = event.event
        room_name = event.room.name if event.room else ""

        logger.info(
            "livekit_webhook_received",
            event_type=event_type,
            room=room_name,
        )

        # Dispatch by event type
        if event_type == "room_started":
            await _handle_room_started(event)
        elif event_type == "room_finished":
            await _handle_room_finished(event)
        elif event_type == "participant_joined":
            _handle_participant_event(event, "joined")
        elif event_type == "participant_left":
            _handle_participant_event(event, "left")
        elif event_type == "egress_ended":
            await _handle_egress_ended(event)

        return Response({"ok": True})


def _extract_call_id(room_name: str) -> str | None:
    """Extract call_id UUID from room name ``call_{uuid}``."""
    match = _ROOM_NAME_RE.match(room_name)
    return match.group(1) if match else None


async def _handle_room_started(event) -> None:
    """Set started_at on the CallExecution if not already set."""
    room_name = event.room.name
    call_id = _extract_call_id(room_name)

    # Fallback for SIP rooms (outbound calls)
    if not call_id:
        call_id = await CallExecutionRepository.get_call_id_by_room_name(room_name)
    if not call_id:
        logger.debug("livekit_webhook_room_started_no_call_id", room=room_name)
        return

    started_at = datetime.fromtimestamp(event.room.creation_time, tz=UTC)
    updated = await CallExecutionRepository.update_lifecycle(
        call_id, started_at=started_at
    )
    if updated:
        logger.info(
            "livekit_webhook_room_started_ok",
            call_id=call_id,
            started_at=started_at.isoformat(),
        )


async def _handle_room_finished(event) -> None:
    """Update lifecycle fields and signal Temporal that the call ended.

    This is the reliable path — fires even if the agent worker crashed.
    Uses the agent worker's ``ended_reason`` if it was written before
    the crash, otherwise defaults to ``"call_completed"``.
    """
    room_name = event.room.name
    call_id = _extract_call_id(room_name)

    # Fallback for SIP rooms (outbound calls) where the room name is
    # auto-generated by the SIP bridge (e.g. ``sip__+1234_xyz``).
    if not call_id:
        call_id = await CallExecutionRepository.get_call_id_by_room_name(room_name)
    if not call_id:
        logger.debug("livekit_webhook_room_finished_no_call_id", room=room_name)
        return

    # Read current state to get ended_reason + workflow_id
    config = await CallExecutionRepository.get_call_config(call_id)
    if not config:
        logger.warning("livekit_webhook_room_finished_no_call", call_id=call_id)
        return

    provider_data = config.get("provider_call_data", {})
    lk_data = provider_data.get(ProviderChoices.LIVEKIT, {})
    workflow_id = lk_data.get("workflow_id", "")

    # Use agent worker's ended_reason if already written, otherwise default
    ended_reason = config.get("ended_reason", "") or "call_completed"

    # Compute timestamps and duration
    now = datetime.now(tz=UTC)
    started_at = None
    if event.room.creation_time:
        started_at = datetime.fromtimestamp(event.room.creation_time, tz=UTC)

    # Prefer the agent worker's session duration (written to DB before the
    # webhook fires) over room lifetime which includes setup overhead.
    agent_duration = config.get("duration_seconds")
    if agent_duration:
        duration_seconds = int(agent_duration)
    elif started_at:
        duration_seconds = int((now - started_at).total_seconds())
    else:
        duration_seconds = 0

    # Update lifecycle
    update_kwargs = {
        "completed_at": now,
        "ended_at": now,
        "duration_seconds": duration_seconds,
        "ended_reason": ended_reason,
    }
    if started_at:
        update_kwargs["started_at"] = started_at

    await CallExecutionRepository.update_lifecycle(call_id, **update_kwargs)

    logger.info(
        "livekit_webhook_room_finished_ok",
        call_id=call_id,
        duration_seconds=duration_seconds,
        ended_reason=ended_reason,
    )

    # Signal Temporal workflow
    await _signal_temporal(call_id, workflow_id, duration_seconds, ended_reason)


async def _handle_egress_ended(event) -> None:
    """Store recording file URL in provider_call_data."""
    egress = event.egress_info
    if not egress:
        return

    room_name = egress.room_name or (event.room.name if event.room else "")
    call_id = _extract_call_id(room_name)
    # Fallback for SIP rooms (outbound calls)
    if not call_id:
        call_id = await CallExecutionRepository.get_call_id_by_room_name(room_name)
    if not call_id:
        logger.debug("livekit_webhook_egress_no_call_id", room=room_name)
        return

    # Extract file URLs from egress results
    file_urls = []
    for f in egress.file_results:
        if f.filename:
            file_urls.append(f.filename)

    config = await CallExecutionRepository.get_call_config(call_id)
    if not config:
        return

    provider_data = config.get("provider_call_data", {})
    lk_data = provider_data.setdefault(ProviderChoices.LIVEKIT, {})
    lk_data["recording_files"] = file_urls
    lk_data["egress_id"] = egress.egress_id
    provider_data[ProviderChoices.LIVEKIT] = lk_data

    await CallExecutionRepository.update_lifecycle(
        call_id, provider_call_data=provider_data
    )
    logger.info(
        "livekit_webhook_egress_ended_ok",
        call_id=call_id,
        egress_id=egress.egress_id,
        file_count=len(file_urls),
    )


def _handle_participant_event(event, action: str) -> None:
    """Log participant join/leave events for debugging."""
    participant = event.participant
    room_name = event.room.name if event.room else ""
    logger.info(
        f"livekit_webhook_participant_{action}",
        room=room_name,
        identity=participant.identity if participant else "",
        kind=str(participant.kind) if participant else "",
    )


async def _signal_temporal(
    call_id: str,
    workflow_id: str,
    duration_seconds: int,
    ended_reason: str,
) -> None:
    """Send call_ended signal to the Temporal workflow."""
    from simulate.models.test_execution import CallExecution

    if not workflow_id:
        from simulate.temporal.constants import CALL_EXECUTION_WORKFLOW_ID_PREFIX

        workflow_id = f"{CALL_EXECUTION_WORKFLOW_ID_PREFIX}-{call_id}"
        logger.warning(
            "livekit_webhook_signal_fallback_workflow_id",
            call_id=call_id,
            workflow_id=workflow_id,
        )

    try:
        from tfc.temporal import get_client

        client = await get_client()
        handle = client.get_workflow_handle(workflow_id=workflow_id)
        await handle.signal(
            "call_ended",
            args=[
                CallExecution.CallStatus.COMPLETED,
                duration_seconds,
                ended_reason,
            ],
        )
        logger.info(
            "livekit_webhook_temporal_signal_sent",
            call_id=call_id,
            workflow_id=workflow_id,
        )
    except Exception:
        logger.exception(
            "livekit_webhook_temporal_signal_failed",
            call_id=call_id,
            workflow_id=workflow_id,
        )

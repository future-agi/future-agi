"""LiveKit SIP helpers — dial out to user's phone for inbound flow."""

from __future__ import annotations

import asyncio
import os
import threading
import time

import structlog
from livekit.api import CreateSIPParticipantRequest  # type: ignore[attr-defined]
from livekit.api import LiveKitAPI

logger = structlog.get_logger(__name__)

# Rate limiter for SIP INVITEs to stay under the SIP trunk CPS (calls per second)
# limit. Allows SIP_CPS_BATCH_SIZE concurrent INVITEs per second.
#
# Uses a thread-safe timestamp instead of asyncio primitives because the
# LiveKit service calls asyncio.run() from sync Temporal activities, creating
# a new event loop each time. Module-level asyncio.Lock/Semaphore objects
# are bound to the loop that existed at import time and fail with
# "bound to a different event loop" when used from a different loop.
_SIP_CPS_BATCH_SIZE = int(os.environ.get("SIP_CPS_BATCH_SIZE", "5"))
_sip_batch_count = 0
_sip_batch_window_start = 0.0
_sip_lock = threading.Lock()


async def _acquire_sip_slot() -> None:
    """Acquire a slot in the current SIP batch.

    Allows up to _SIP_CPS_BATCH_SIZE calls to fire concurrently per second.
    If the batch is full, sleeps until the next 1-second window.
    """
    global _sip_batch_count, _sip_batch_window_start

    while True:
        with _sip_lock:
            now = time.monotonic()
            # Reset batch if 1 second has passed since window start
            if now - _sip_batch_window_start >= 1.0:
                _sip_batch_count = 0
                _sip_batch_window_start = now

            if _sip_batch_count < _SIP_CPS_BATCH_SIZE:
                _sip_batch_count += 1
                return

            # Batch full — calculate how long to wait
            wait = 1.0 - (now - _sip_batch_window_start)

        if wait > 0:
            await asyncio.sleep(wait)


async def create_sip_participant(
    lkapi: LiveKitAPI,
    sip_trunk_id: str,
    phone_number: str,
    room_name: str,
    participant_identity: str = "phone-user",
    participant_name: str = "Phone User",
    dtmf: str = "",
) -> str:
    """Dial out via SIP trunk to *phone_number* and place into *room_name*.

    Applies batch rate limiting: up to SIP_CPS_BATCH_SIZE calls fire
    concurrently per second window. Default batch size is 5.
    Returns the SIP participant ID.
    """
    await _acquire_sip_slot()

    resp = await lkapi.sip.create_sip_participant(
        CreateSIPParticipantRequest(
            sip_trunk_id=sip_trunk_id,
            sip_call_to=phone_number,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_name=participant_name,
            dtmf=dtmf,
        )
    )
    logger.info(
        "livekit_sip_participant_created",
        room_name=room_name,
        phone_number=phone_number,
        participant_id=resp.participant_id,
    )
    return resp.participant_id

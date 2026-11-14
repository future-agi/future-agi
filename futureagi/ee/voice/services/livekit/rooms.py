"""LiveKit room CRUD and stale room cleanup helpers."""

from __future__ import annotations

import time

import structlog
from livekit.api import (  # type: ignore[attr-defined]
    CreateRoomRequest,
    DeleteRoomRequest,
    ListRoomsRequest,
    LiveKitAPI,
)

logger = structlog.get_logger(__name__)


async def create_room(
    lkapi: LiveKitAPI,
    room_name: str,
    empty_timeout: int = 300,
    max_participants: int = 2,
    metadata: str = "",
):
    """Create a LiveKit room. Returns the Room proto."""
    room = await lkapi.room.create_room(
        CreateRoomRequest(
            name=room_name,
            empty_timeout=empty_timeout,
            max_participants=max_participants,
            metadata=metadata,
        )
    )
    logger.info("livekit_room_created", room_name=room_name)
    return room


async def delete_room(lkapi: LiveKitAPI, room_name: str) -> None:
    """Delete a LiveKit room by name."""
    await lkapi.room.delete_room(DeleteRoomRequest(room=room_name))
    logger.info("livekit_room_deleted", room_name=room_name)


async def delete_stale_rooms(
    lkapi: LiveKitAPI,
    min_age_seconds: int = 300,
    room_prefix: str = "call_",
) -> int:
    """Delete stale rooms — rooms with <2 participants for over *min_age_seconds*.

    A room is stale if it was created more than *min_age_seconds* ago and still
    has fewer than 2 participants (agent + SIP caller never fully connected).
    """
    resp = await lkapi.room.list_rooms(ListRoomsRequest())
    now = time.time()
    deleted = 0
    for room in resp.rooms:
        if not room.name.startswith(room_prefix):
            continue
        age = now - room.creation_time
        if room.num_participants < 2 and age > min_age_seconds:
            logger.info(
                "livekit_deleting_stale_room",
                room_name=room.name,
                num_participants=room.num_participants,
                age_seconds=int(age),
            )
            await delete_room(lkapi, room.name)
            deleted += 1
    if deleted:
        logger.info("livekit_stale_rooms_deleted", count=deleted)
    return deleted

"""Resolve the platform's existing outbound telephony configuration for ALK.

The hosted runner already uses LIVEKIT_OUTBOUND_TRUNK_ID and
PSTN_CALLER_NUMBER for phone agent definitions. ALK's call runner uses
different wire names; resolve those names here without asking the customer
for another trunk or caller ID.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jwt
import requests
from django.conf import settings

_LIVEKIT_CLEANUP_TIMEOUT_SECONDS = 10


def platform_phone_telephony() -> dict[str, str]:
    def value(*names: str) -> str:
        for name in names:
            configured = str(
                getattr(settings, name, None) or os.environ.get(name) or ""
            ).strip()
            if configured:
                return configured
        return ""

    return {
        "LIVEKIT_URL": value("LIVEKIT_URL"),
        "LIVEKIT_API_KEY": value("LIVEKIT_API_KEY"),
        "LIVEKIT_API_SECRET": value("LIVEKIT_API_SECRET"),
        "SIP_OUTBOUND_TRUNK_ID": value(
            "LIVEKIT_OUTBOUND_TRUNK_ID", "SIP_OUTBOUND_TRUNK_ID"
        ),
        "SIP_OUTBOUND_FROM_NUMBER": value(
            "PSTN_CALLER_NUMBER", "SIP_OUTBOUND_FROM_NUMBER"
        ),
    }


def uses_platform_phone_telephony(payload: Mapping[str, Any]) -> bool:
    """Return whether a hosted job dials a PSTN target with platform credentials."""

    agent = payload.get("agent")
    if not isinstance(agent, Mapping):
        return False
    connector = str(agent.get("connector") or "").strip().lower()
    mode = str(agent.get("mode") or "").strip().lower()
    config = agent.get("config")
    config = config if isinstance(config, Mapping) else {}
    return connector == "phone" or (
        connector in {"vapi", "retell"}
        and mode == "connect_only"
        and bool(str(config.get("phone_number") or "").strip())
    )


@dataclass(frozen=True)
class HostedPhoneRoomCleanup:
    matched_rooms: tuple[str, ...]
    deleted_rooms: tuple[str, ...]


class HostedPhoneRoomCleanupError(RuntimeError):
    """Raised while a cancelled phone run still owns a LiveKit room."""


def _livekit_http_url(url: str) -> str:
    return url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def _livekit_room_token(*, api_key: str, api_secret: str, method: str) -> str:
    now = int(time.time())
    # LiveKit authorizes room deletion with ``roomCreate``. ``roomAdmin`` is
    # for participant/track administration and returns 401 for DeleteRoom.
    grant: dict[str, Any] = (
        {"roomCreate": True} if method == "DeleteRoom" else {"roomList": True}
    )
    return jwt.encode(
        {
            "iss": api_key,
            "sub": "",
            "nbf": now,
            "exp": now + 60,
            "video": grant,
        },
        api_secret,
        algorithm="HS256",
    )


def _livekit_room_request(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    method: str,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    response = requests.post(
        f"{_livekit_http_url(url)}/twirp/livekit.RoomService/{method}",
        headers={
            "Authorization": (
                "Bearer "
                + _livekit_room_token(
                    api_key=api_key,
                    api_secret=api_secret,
                    method=method,
                )
            ),
            "Content-Type": "application/json",
        },
        json=dict(body),
        timeout=_LIVEKIT_CLEANUP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json() if response.content else {}
    return payload if isinstance(payload, dict) else {}


def _cleanup_hosted_phone_rooms(
    *, room_prefixes: tuple[str, ...], url: str, api_key: str, api_secret: str
) -> HostedPhoneRoomCleanup:
    matched: list[str] = []
    deleted: list[str] = []
    deletion_errors: dict[str, Exception] = {}
    response = _livekit_room_request(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
        method="ListRooms",
        body={},
    )
    matched = sorted(
        str(room.get("name") or "")
        for room in response.get("rooms", [])
        if isinstance(room, Mapping)
        and str(room.get("name") or "").startswith(room_prefixes)
    )
    for room_name in matched:
        try:
            _livekit_room_request(
                url=url,
                api_key=api_key,
                api_secret=api_secret,
                method="DeleteRoom",
                body={"room": room_name},
            )
            deleted.append(room_name)
        except Exception as exc:  # verified below; a concurrent delete is success
            deletion_errors[room_name] = exc

    verification = _livekit_room_request(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
        method="ListRooms",
        body={},
    )
    remaining = sorted(
        str(room.get("name") or "")
        for room in verification.get("rooms", [])
        if isinstance(room, Mapping)
        and str(room.get("name") or "").startswith(room_prefixes)
    )
    if remaining:
        failed = ", ".join(remaining)
        causes = "; ".join(
            f"{name}: {type(deletion_errors[name]).__name__}"
            for name in remaining
            if name in deletion_errors
        )
        suffix = f" ({causes})" if causes else ""
        raise HostedPhoneRoomCleanupError(
            f"LiveKit rooms still active after hosted cancellation: {failed}{suffix}"
        )
    return HostedPhoneRoomCleanup(tuple(matched), tuple(deleted))


def cleanup_hosted_phone_rooms(job) -> HostedPhoneRoomCleanup:
    """Hang up every platform-owned phone room for one hosted execution.

    Room deletion disconnects the SIP participant. Platform-generated job and
    execution identifiers scope the prefixes to the cancelled hosted run.
    """

    if not uses_platform_phone_telephony(job.payload or {}):
        return HostedPhoneRoomCleanup((), ())
    job_id = str(getattr(job, "id", None) or "").strip()
    execution_id = str(getattr(job, "test_execution_id", None) or "").strip()
    if not job_id and not execution_id:
        return HostedPhoneRoomCleanup((), ())

    telephony = platform_phone_telephony()
    required = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    missing = [name for name in required if not telephony.get(name)]
    if missing:
        raise HostedPhoneRoomCleanupError(
            "Cannot verify hosted phone cancellation without " + ", ".join(missing)
        )

    # There are two hosted voice execution lanes. The released ALK harness uses
    # ``harness-<job UUID prefix>-...`` while the platform-native hosted runner
    # uses ``hosted-<test execution UUID>-...``. Cancellation owns both lanes.
    room_prefixes = tuple(
        prefix
        for prefix in (
            f"harness-{job_id[:8]}-" if job_id else "",
            f"hosted-{execution_id}-" if execution_id else "",
        )
        if prefix
    )
    return _cleanup_hosted_phone_rooms(
        room_prefixes=room_prefixes,
        url=telephony["LIVEKIT_URL"],
        api_key=telephony["LIVEKIT_API_KEY"],
        api_secret=telephony["LIVEKIT_API_SECRET"],
    )

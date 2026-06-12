from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from django.db.models import F

from accounts.models.user import User
from tfc.deployment_telemetry.config import (
    MAX_PAYLOAD_BYTES,
    MAX_REGISTRATION_USERS,
    detect_deployment_type,
    get_version,
)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def serialized_size(payload: dict) -> int:
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    )


def build_minimal_registration_payload(
    instance_id: UUID,
    timestamp: datetime | None = None,
) -> dict:
    return {
        "instance_id": str(instance_id),
        "version": get_version(),
        "deployment_type": detect_deployment_type(),
        "timestamp": format_utc(timestamp or datetime.now(UTC)),
        "telemetry_disabled": True,
    }


def build_full_registration_payload(
    instance_id: UUID,
    timestamp: datetime | None = None,
) -> dict | None:
    payload = {
        "instance_id": str(instance_id),
        "version": get_version(),
        "deployment_type": detect_deployment_type(),
        "timestamp": format_utc(timestamp or datetime.now(UTC)),
        "telemetry_disabled": False,
        "users": [],
    }

    users = User.objects.filter(is_active=True).order_by(
        F("last_login").desc(nulls_last=True),
        "-created_at",
    )[:MAX_REGISTRATION_USERS]

    for user in users:
        email = user.email.strip().lower()
        if "@" not in email:
            continue
        entry = {"email": email, "domain": email.rsplit("@", 1)[1]}
        payload["users"].append(entry)
        if serialized_size(payload) > MAX_PAYLOAD_BYTES:
            payload["users"].pop()
            break

    if not payload["users"]:
        return None
    return payload


def build_heartbeat_payload(
    instance_id: UUID,
    window_start: datetime,
    window_end: datetime,
    counts: dict[str, int],
) -> dict:
    return {
        "instance_id": str(instance_id),
        "version": get_version(),
        "window_start": format_utc(window_start),
        "window_end": format_utc(window_end),
        **counts,
    }

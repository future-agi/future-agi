from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from django.db.models import F, Q

from accounts.models.user import User
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.deployment_telemetry.config import (
    detect_deployment_type,
    get_version,
)
from tfc.deployment_telemetry.schema import (
    MAX_PAYLOAD_BYTES,
    MAX_REGISTRATION_USERS,
    SCHEMA_VERSION,
    derive_domain,
)


class _RegistrationUser(TypedDict):
    email: str
    domain: str


class MinimalRegistrationPayload(TypedDict):
    """Wire shape sent when telemetry is disabled (census ping only)."""

    schema_version: int
    instance_id: str
    version: str
    deployment_type: str
    timestamp: str
    telemetry_disabled: bool


class FullRegistrationPayload(TypedDict):
    """Wire shape sent on enabled registration (with admin user emails)."""

    schema_version: int
    instance_id: str
    version: str
    deployment_type: str
    timestamp: str
    telemetry_disabled: bool
    users: list[_RegistrationUser]


class HeartbeatPayload(TypedDict, total=False):
    """Wire shape for a heartbeat window. COUNT_FIELDS appear at top level
    alongside the fixed metadata keys; total=False so a count missing from
    a particular cycle's collectors doesn't trip the type."""

    schema_version: int
    instance_id: str
    version: str
    window_start: str
    window_end: str


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def serialized_size(payload: dict) -> int:
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    )


def build_minimal_registration_payload(
    instance_id: UUID,
    timestamp: datetime | None = None,
) -> MinimalRegistrationPayload:
    return {
        "schema_version": SCHEMA_VERSION,
        "instance_id": str(instance_id),
        "version": get_version(),
        "deployment_type": detect_deployment_type(),
        "timestamp": format_utc(timestamp or datetime.now(UTC)),
        "telemetry_disabled": True,
    }


def build_full_registration_payload(
    instance_id: UUID,
    timestamp: datetime | None = None,
) -> FullRegistrationPayload | None:
    payload: FullRegistrationPayload = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": str(instance_id),
        "version": get_version(),
        "deployment_type": detect_deployment_type(),
        "timestamp": format_utc(timestamp or datetime.now(UTC)),
        "telemetry_disabled": False,
        "users": [],
    }

    users = (
        User.objects.filter(is_active=True)
        .filter(
            Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(organization_role=OrganizationRoles.OWNER)
            | Q(organization_role=OrganizationRoles.ADMIN)
            | Q(
                organization_memberships__is_active=True,
                organization_memberships__level__gte=Level.ADMIN,
            )
            | Q(
                organization_memberships__is_active=True,
                organization_memberships__level__isnull=True,
                organization_memberships__role__in=[
                    OrganizationRoles.OWNER,
                    OrganizationRoles.ADMIN,
                ],
            )
        )
        .distinct()
        .order_by(
            F("last_login").desc(nulls_last=True),
            "-created_at",
        )[:MAX_REGISTRATION_USERS]
    )

    # Re-encoding the whole payload after every appended user is O(N²) in
    # both the user count and the average entry size; at the 500-user cap
    # that's ~500 full json.dumps per registration. Track a running budget
    # against per-entry size instead: each entry adds its own bytes plus a
    # one-byte ``,`` separator (the first entry skips the comma; the empty
    # ``[]`` already sits inside the base payload size).
    base_size = serialized_size(payload)
    budget = MAX_PAYLOAD_BYTES - base_size
    for user in users:
        email = user.email.strip().lower()
        if "@" not in email:
            continue
        entry = {"email": email, "domain": derive_domain(email)}
        entry_bytes = len(
            json.dumps(entry, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )
        cost = entry_bytes if not payload["users"] else entry_bytes + 1
        if cost > budget:
            break
        payload["users"].append(entry)
        budget -= cost

    if not payload["users"]:
        return None
    return payload


def build_heartbeat_payload(
    instance_id: UUID,
    window_start: datetime,
    window_end: datetime,
    counts: dict[str, int | None],
) -> HeartbeatPayload:
    return {
        "schema_version": SCHEMA_VERSION,
        "instance_id": str(instance_id),
        "version": get_version(),
        "window_start": format_utc(window_start),
        "window_end": format_utc(window_end),
        **counts,
    }

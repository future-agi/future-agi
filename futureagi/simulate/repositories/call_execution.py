"""Repository for CallExecution model — encapsulates all ORM access."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from simulate.models.test_execution import CallExecution
from simulate.utils.ended_reason import set_ended_reason


class CallExecutionRepository:
    """Data access layer for CallExecution."""

    @staticmethod
    async def get_call_config(call_id: UUID) -> dict[str, Any] | None:
        """Return call config fields needed by the agent worker.

        Returns None if the call execution doesn't exist.
        """
        try:
            ce = await CallExecution.objects.aget(id=call_id)
        except CallExecution.DoesNotExist:
            return None
        return {
            "id": str(ce.id),
            "call_metadata": ce.call_metadata or {},
            "provider_call_data": ce.provider_call_data or {},
            "status": ce.status,
            "ended_reason": ce.ended_reason or "",
            "duration_seconds": ce.duration_seconds,
        }

    @staticmethod
    async def get_call_id_by_room_name(room_name: str) -> str | None:
        """Look up a CallExecution by its service_provider_call_id (room name).

        Used by webhook handler for SIP rooms where the room name doesn't
        contain the call_id (e.g. ``sip__+1234_xyz`` vs ``call_{uuid}``).
        """
        try:
            ce = await CallExecution.objects.filter(
                service_provider_call_id=room_name,
            ).afirst()
        except Exception:
            return None
        return str(ce.id) if ce else None

    @staticmethod
    async def update_lifecycle(
        call_id: UUID,
        *,
        provider_call_data: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        ended_at: datetime | None = None,
        duration_seconds: int | None = None,
        ended_reason: str | None = None,
        service_provider_call_id: str | None = None,
        ended_reason_source: str | None = None,
    ) -> bool:
        """Update lifecycle fields on a CallExecution.

        Only fields that are not None are updated. Returns True if the
        row was found and updated, False otherwise. ``ended_reason`` needs
        ``ended_reason_source``, an ``EndedReasonSource`` value chosen by the
        caller's own code (simulate/utils/ended_reason.py).
        """
        if ended_reason is not None and ended_reason_source is None:
            raise ValueError("ended_reason needs an ended_reason_source")
        try:
            ce = await CallExecution.objects.aget(id=call_id)
        except CallExecution.DoesNotExist:
            return False

        update_fields: list[str] = []

        if provider_call_data is not None:
            # Deep-merge into existing data so agent worker can add keys
            # (e.g. egress_id) without overwriting backend-set values.
            existing = ce.provider_call_data or {}
            for key, val in provider_call_data.items():
                if isinstance(val, dict) and isinstance(existing.get(key), dict):
                    existing[key].update(val)
                else:
                    existing[key] = val
            ce.provider_call_data = existing
            update_fields.append("provider_call_data")
        if started_at is not None:
            ce.started_at = started_at
            update_fields.append("started_at")
        if completed_at is not None:
            ce.completed_at = completed_at
            update_fields.append("completed_at")
        if ended_at is not None:
            ce.ended_at = ended_at
            update_fields.append("ended_at")
        if duration_seconds is not None:
            ce.duration_seconds = duration_seconds
            update_fields.append("duration_seconds")
        if ended_reason is not None:
            update_fields.extend(
                set_ended_reason(ce, ended_reason, ended_reason_source)
            )
        if service_provider_call_id is not None:
            ce.service_provider_call_id = service_provider_call_id
            update_fields.append("service_provider_call_id")

        if update_fields:
            await ce.asave(update_fields=update_fields)

        return True

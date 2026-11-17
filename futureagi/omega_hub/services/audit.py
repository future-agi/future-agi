"""Writes to the append-only audit log (SPEC §8). One call per audited action."""

from __future__ import annotations

from omega_hub import pseudonym
from omega_hub.models import AuditEvent


def record(action: str, *, target_type: str, target_id, actor=None, actor_kind: str | None = None,
           publisher=None, data: dict | None = None) -> AuditEvent:
    if actor_kind is None:
        actor_kind = AuditEvent.ActorKind.USER if actor is not None else AuditEvent.ActorKind.SYSTEM
    actor = actor if actor is not None and getattr(actor, "pk", None) else None
    return AuditEvent.objects.create(
        actor=actor,
        actor_label=pseudonym.label_for(actor),
        actor_kind=actor_kind,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        publisher=publisher,
        data=data or {},
    )


def record_checker(action: str, *, target_type: str, target_id, publisher=None, data: dict | None = None):
    return record(action, target_type=target_type, target_id=target_id, actor=None,
                  actor_kind=AuditEvent.ActorKind.CHECKER, publisher=publisher, data=data)

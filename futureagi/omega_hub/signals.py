"""Audit a `hub_maintainer` grant or revoke made anywhere, including Django admin (SPEC §5.1).

This receiver is the single writer of `maintainer.granted` / `maintainer.revoked`. The management
command sets `via` and `by` through `grant_context` so its own change is recorded once, with them.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("omega_hub_grant", default=None)


@contextmanager
def grant_context(*, via: str, by=None):
    token = _context.set({"via": via, "by": by})
    try:
        yield
    finally:
        _context.reset(token)


def _is_maintainer_perm(pk_set) -> bool:
    from omega_hub.permissions import maintainer_permission

    if not pk_set:
        return False
    try:
        return maintainer_permission().pk in pk_set
    except Exception:
        return False


@receiver(m2m_changed, sender=get_user_model().user_permissions.through)
def audit_maintainer_grant(sender, instance, action, reverse, model, pk_set, **kwargs):
    if reverse or action not in ("post_add", "post_remove") or not _is_maintainer_perm(pk_set):
        return
    from omega_hub.services import audit

    ctx = _context.get() or {"via": "django-admin", "by": None}
    audit.record(
        "maintainer.granted" if action == "post_add" else "maintainer.revoked",
        target_type="user",
        target_id=instance.pk,
        actor=ctx.get("by"),
        data={"via": ctx["via"], "email": instance.email},
    )

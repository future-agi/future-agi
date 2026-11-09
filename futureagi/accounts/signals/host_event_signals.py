"""Deliver a revocation as soon as the change that caused it commits (READINESS lane 1.2a).

Capture does not depend on these signals: Postgres triggers write the outbox row in the
writer's transaction for every path (``accounts.host_events.capture_sql``). The signals
only make delivery fast for ``save()`` and ``delete()`` paths: after the transaction
commits, one drain runs in a short-lived thread, so the admin's request never waits on
EE and a stream ends at its next 30 s re-check. Bulk ``QuerySet.update()`` paths fire
no signal; the 3 s schedule delivers those.
"""

import threading

import structlog
from django.db import close_old_connections, connection, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = structlog.get_logger(__name__)

_ACCESS_FIELDS = frozenset({"is_active", "deleted", "level", "role"})
_running = threading.Lock()
_again = threading.Event()


def _drain_in_background():
    # One drain thread per process; a kick while it runs makes it go round once more.
    if not _running.acquire(blocking=False):
        _again.set()
        return

    def run():
        try:
            from accounts.host_events.relay import drain

            while True:
                _again.clear()
                close_old_connections()
                drain(budget_seconds=5.0, purge_old=False)
                if not _again.is_set():
                    break
        except Exception:
            # The schedule retries; a failed fast path must never surface in the request.
            logger.exception("omega_host_events_fast_drain_failed")
        finally:
            connection.close()
            _running.release()

    threading.Thread(target=run, name="omega-host-events", daemon=True).start()


def kick():
    if connection.vendor != "postgresql":
        return
    from accounts.host_events.config import READY
    from accounts.host_events.relay import current_config

    # Only a process that can send starts a drain; the schedule covers the rest.
    if current_config().state == READY:
        transaction.on_commit(_drain_in_background)


def _relevant(update_fields):
    return update_fields is None or bool(_ACCESS_FIELDS & set(update_fields))


@receiver(post_save, sender="accounts.OrganizationMembership")
@receiver(post_save, sender="accounts.WorkspaceMembership")
def membership_saved(sender, instance, created, update_fields=None, **kwargs):
    if not created and _relevant(update_fields):
        kick()


@receiver(post_save, sender="accounts.User")
def user_saved(sender, instance, created, update_fields=None, **kwargs):
    if (
        not created
        and not instance.is_active
        and (update_fields is None or "is_active" in update_fields)
    ):
        kick()


@receiver(post_delete, sender="accounts.OrganizationMembership")
@receiver(post_delete, sender="accounts.WorkspaceMembership")
@receiver(post_delete, sender="accounts.User")
@receiver(post_delete, sender="accounts.Workspace")
@receiver(post_delete, sender="accounts.Organization")
def access_deleted(sender, instance, **kwargs):
    kick()


@receiver(post_save, sender="accounts.Workspace")
def workspace_saved(sender, instance, created, update_fields=None, **kwargs):
    # READINESS lane 3.22: a workspace's soft delete starts EE's purge without waiting for the
    # schedule. Capture itself is the trigger's (accounts.lifecycle.capture_sql).
    if not created and (not instance.is_active or instance.deleted):
        kick()

"""Keep open sockets in step with organization membership.

Each organization-bound socket (``DataConsumer``, ``SimulationUpdateConsumer``,
``FalconAIConsumer``) joins a per-user group when it connects. When one of the
user's memberships ends, ``close_sockets_for_membership`` asks those sockets to
revalidate; a socket that no longer belongs to its organization closes itself.
``MembershipSocketGuard`` also revalidates before delivery on a short interval,
which covers changes made without a notification (bulk updates, other
processes).
"""

from __future__ import annotations

import time
from functools import partial

import structlog
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction

from sockets.org_scope import organization_membership_is_active

logger = structlog.get_logger(__name__)

# Close code for a socket whose organization no longer authorizes the user.
# Shared with the connect refusal and the frontend's no-reconnect list.
MEMBERSHIP_CLOSE_CODE = 4002
MEMBERSHIP_CHANGED_EVENT = "membership.changed"
DEFAULT_REVALIDATE_SECONDS = 10


def user_socket_group(user_id) -> str:
    """Return the group every relay socket of this user joins."""

    return f"socket_user_{user_id}"


def close_sockets_for_membership(user_id, organization_id=None) -> None:
    """After commit, close the user's open sockets that lost their organization.

    ``organization_id`` limits the check to sockets bound to that organization;
    ``None`` asks every socket of the user to revalidate. Each socket re-reads
    the membership before closing, so a call for a membership that is still
    active changes nothing. Never raises into the caller.
    """

    if user_id is None:
        return
    organization_id = str(organization_id) if organization_id is not None else None
    try:
        transaction.on_commit(
            partial(_notify_user_sockets, str(user_id), organization_id), robust=True
        )
    except Exception as exc:  # noqa: BLE001 - membership changes must not fail on this
        logger.warning("socket_membership_notify_failed", error_type=type(exc).__name__)


def _notify_user_sockets(user_id: str, organization_id: str | None) -> None:
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            user_socket_group(user_id),
            {"type": MEMBERSHIP_CHANGED_EVENT, "organization_id": organization_id},
        )
    except Exception as exc:  # noqa: BLE001 - delivery revalidation still applies
        logger.warning("socket_membership_notify_failed", error_type=type(exc).__name__)


class MembershipSocketGuard:
    """Revalidate a relay socket's organization membership before delivery.

    Mirrors ``SamlSocketGuard.ensure_live``: deliveries reuse a recent result
    for a short interval, and a notification forces a fresh check. SAML
    sockets are revalidated by their own guard, which already requires an
    active membership, so only forced checks run for them here.

    A forced check that does not complete (for example a database error)
    leaves the socket pending: every delivery, SAML or not, then checks again
    and nothing is delivered until a check succeeds.
    """

    def __init__(self, consumer):
        self.consumer = consumer
        self.closed = False
        self._last_validated_at: float | None = None
        self._revalidation_pending = False
        # Advanced by every forced check. Only a check that started at the
        # current generation may clear a pending revalidation, so a check that
        # was already running when the membership changed cannot.
        self._generation = 0

    @property
    def revalidation_pending(self) -> bool:
        return self._revalidation_pending

    @property
    def generation(self) -> int:
        """Advanced by every forced check."""

        return self._generation

    def mark_validated(self, at: float | None = None) -> None:
        self._last_validated_at = time.monotonic() if at is None else at

    async def ensure_member(self, *, force: bool = False) -> bool:
        """Return whether the socket may deliver, closing it when it may not."""

        if self.closed:
            return False
        organization_id = getattr(self.consumer, "organization_id", None)
        user = getattr(self.consumer, "user", None)
        if organization_id is None or user is None:
            return False

        now = time.monotonic()
        if force:
            # Cleared only by a successful check, so an interrupted or failed
            # check cannot fall back to a cached result.
            self._revalidation_pending = True
            self._generation += 1
        elif not self._revalidation_pending:
            if self.consumer.scope.get("auth_scope") is not None:
                return True
            interval = getattr(
                settings,
                "SOCKET_MEMBERSHIP_REVALIDATE_SECONDS",
                DEFAULT_REVALIDATE_SECONDS,
            )
            if (
                self._last_validated_at is not None
                and now - self._last_validated_at < interval
            ):
                return True

        generation = self._generation
        try:
            allowed = await database_sync_to_async(organization_membership_is_active)(
                user.id, organization_id
            )
        except Exception as exc:  # noqa: BLE001 - skip delivery until a check succeeds
            self._last_validated_at = None
            logger.warning(
                "socket_membership_check_failed", error_type=type(exc).__name__
            )
            return False

        if self.closed:
            return False
        if generation != self._generation:
            # The membership changed while this check ran; its answer may
            # predate the change, so it neither delivers, closes nor clears
            # the pending revalidation.
            return False
        if not allowed:
            await self._close()
            return False
        self._last_validated_at = now
        self._revalidation_pending = False
        return True

    async def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        logger.info(
            "socket_membership_closed",
            organization_id=str(self.consumer.organization_id),
            user_id=str(self.consumer.user.id),
        )
        await self.consumer.close(code=MEMBERSHIP_CLOSE_CODE)

"""Database-authoritative liveness checks for SAML WebSocket sessions."""

from __future__ import annotations

import asyncio
import time

import structlog
from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

from accounts.models.auth_token import AuthToken
from accounts.saml_credential import SamlCredentialInvalid, validate_saml_credential

security_logger = structlog.get_logger("saml2_auth.security")


# Close code when the credential could not be checked for longer than the
# unavailable window (database or cache outage). The sign-in was never refused,
# so the client reconnects with backoff, as for the handshake in
# tfc/middleware/jwt_auth.py.
UNAVAILABLE_CLOSE_CODE = 1011
DEFAULT_UNAVAILABLE_CLOSE_SECONDS = 60


class SamlSocketGuard:
    """Revalidate SAML credentials before inbound frames and periodic pushes.

    A refused credential closes the socket with 4001. A check that cannot
    complete (for example a database error) refuses nothing: the socket stays
    open but pending, so no frame is delivered or answered until a check
    succeeds. If checks keep failing for longer than
    ``SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS``, the socket closes with 1011.
    """

    def __init__(self, consumer):
        self.consumer = consumer
        self.revoked = False
        self.closed = False
        self._last_validated_at: float | None = None
        self._validated_once = False
        self._unavailable_since: float | None = None
        self._generation = 0
        # Checks again once the window ends, so a pending socket that
        # receives no traffic still closes.
        self._window_timer: asyncio.TimerHandle | None = None
        self._window_task: asyncio.Task | None = None
        # The window check that succeeded and is now letting the consumer
        # answer what it kept; a disconnect cancels it.
        self._recovery_task: asyncio.Task | None = None

    @property
    def pending(self) -> bool:
        """Whether the last check could not complete."""

        return self._unavailable_since is not None

    @property
    def generation(self) -> int:
        """Advanced by ``expire``; a check started before then is stale."""

        return self._generation

    def stop(self) -> None:
        """Cancel the scheduled window check and a running recovery; the
        socket has disconnected."""

        self.closed = True
        self._cancel_window_check()
        task = self._recovery_task
        self._recovery_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def expire(self) -> None:
        """Make the next push revalidate instead of reusing a recent result."""

        self._last_validated_at = None
        # A check already running started before this call; it must not
        # record its result as recent.
        self._generation += 1

    async def ensure_live(self, *, inbound: bool) -> bool:
        """Return whether the socket remains usable, closing revoked sessions."""

        auth_scope = self.consumer.scope.get("auth_scope")
        if auth_scope is None:
            return True
        if self.revoked or self.closed:
            return False

        now_monotonic = time.monotonic()
        interval = getattr(settings, "SAML_SOCKET_REVALIDATE_SECONDS", 30)
        if (
            not inbound
            and self._last_validated_at is not None
            and now_monotonic - self._last_validated_at < interval
        ):
            return True

        generation = self._generation
        try:
            await self._validate()
        except SamlCredentialInvalid as exc:
            await self._revoke(exc.reason)
            return False
        except Exception as exc:  # noqa: BLE001 - unchecked is not refused
            await self._check_unavailable(type(exc).__name__)
            return False

        if self.revoked or self.closed:
            return False
        self._validated_once = True
        self._unavailable_since = None
        self._cancel_window_check()
        if generation == self._generation:
            self._last_validated_at = now_monotonic
        return True

    async def _check_unavailable(self, error_type: str) -> None:
        """Keep the socket pending, closing it with 1011 after the window."""

        # Nothing may reuse an earlier result while the credential is unchecked.
        self._last_validated_at = None
        now = time.monotonic()
        if self._unavailable_since is None:
            self._unavailable_since = now
        window = getattr(
            settings,
            "SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS",
            DEFAULT_UNAVAILABLE_CLOSE_SECONDS,
        )
        # Before the first successful check the handshake is still waiting,
        # so there is nothing to keep open.
        connecting = not self._validated_once
        security_logger.warning(
            "saml_socket_check_unavailable",
            error_type=error_type,
            transport="websocket",
        )
        elapsed = now - self._unavailable_since
        if not connecting and elapsed < window:
            self._schedule_window_check(window - elapsed)
            return
        if self.closed or self.revoked:
            return
        self.closed = True
        self._cancel_window_check()
        if connecting:
            # Accept first so the client sees 1011 rather than a refused
            # handshake, as the authentication middleware does.
            await self.consumer.accept()
        await self.consumer.close(code=UNAVAILABLE_CLOSE_CODE)

    @database_sync_to_async
    def _validate(self):
        token_id = self.consumer.scope.get("auth_token_id")
        token_row = (
            AuthToken.objects.filter(id=token_id)
            .values(
                "id",
                "user_id",
                "auth_type",
                "is_active",
                "last_used_at",
                "scoped_organization_id",
                "origin_idp_id",
                "origin_idp_generation",
            )
            .first()
        )
        now = timezone.now()
        validate_saml_credential(
            token_row,
            str(self.consumer.scope["user"].id),
            now=now,
            transport="websocket",
        )
        AuthToken.objects.filter(id=token_id, is_active=True).update(last_used_at=now)

    def _schedule_window_check(self, delay: float) -> None:
        if self._window_timer is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._window_timer = loop.call_later(max(delay, 0.05), self._start_window_check)

    def _start_window_check(self) -> None:
        self._window_timer = None
        if self.pending and not (self.revoked or self.closed):
            self._window_task = asyncio.ensure_future(self._window_check())

    async def _window_check(self) -> None:
        try:
            if await self.ensure_live(inbound=True):
                # No client frame or push may follow the recovery, so let the
                # consumer answer what it kept while the check was pending.
                # The success above already released this task as the window
                # check, so keep it where stop() can cancel it.
                self._recovery_task = asyncio.current_task()
                try:
                    await self._notify_consumer("_on_saml_check_recovered")
                finally:
                    if self._recovery_task is asyncio.current_task():
                        self._recovery_task = None
        except Exception as exc:  # noqa: BLE001 - the socket may be gone
            security_logger.warning(
                "saml_socket_window_check_failed",
                error_type=type(exc).__name__,
                transport="websocket",
            )

    def _cancel_window_check(self) -> None:
        if self._window_timer is not None:
            self._window_timer.cancel()
            self._window_timer = None
        task = self._window_task
        self._window_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _revoke(self, reason: str) -> None:
        if self.revoked or self.closed:
            return
        self.revoked = True
        self._cancel_window_check()
        security_logger.warning(
            "saml_socket_revoked",
            reason=reason,
            transport="websocket",
        )
        await self.consumer.close(code=4001)
        # Whichever path found the refusal (a frame, a push or the window
        # timer), the consumer stops its work for this sign-in now.
        await self._notify_consumer("_on_saml_revoked")

    async def _notify_consumer(self, hook_name: str) -> None:
        """Await the consumer's optional hook; a failing hook is logged."""

        hook = getattr(self.consumer, hook_name, None)
        if hook is None:
            return
        try:
            await hook()
        except Exception as exc:  # noqa: BLE001 - the guard's state is already final
            security_logger.warning(
                "saml_socket_hook_failed",
                hook=hook_name,
                error_type=type(exc).__name__,
                transport="websocket",
            )

"""The interfaces the core needs (DESIGN.md section 22.1): ``Clock``, ``Resolver``, ``Transport``, ``Store`` and
``Queue``. The Django app supplies the hosted adapters (ORM store, ``pg_notify`` queue, the hardened urllib3 transport
from ``tfc/utils/ssrf_guard.py``); an embedded build supplies its own and emits the same signed bytes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now_ms(self) -> int:
        """Wall-clock milliseconds since the epoch (``t``, schedules, leases)."""

    def monotonic(self) -> float:
        """Seconds on a monotonic clock (attempt deadlines)."""


class ResolveError(Exception):
    """The name did not resolve inside its budget; the attempt is ``dns_error``."""


@runtime_checkable
class Resolver(Protocol):
    def resolve(self, host: str, timeout_s: float) -> list[str]:
        """A and AAAA answers for the ABSOLUTE name (no search list), in answer order. Raises ResolveError."""


class AbortHandle:
    """Lets the dispatcher stop an attempt at once: when a lease renewal touches no row, another worker owns the
    delivery, and the sender closes its socket instead of waiting for the deadline (section 13.1, R2). The transport
    binds a callback (shut the socket down); :meth:`abort` runs it, now or as soon as it is bound."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._aborted = False
        self._callbacks: list[Callable[[], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    def bind(self, callback: Callable[[], None]) -> None:
        with self._lock:
            run_now = self._aborted
            if not run_now:
                self._callbacks.append(callback)
        if run_now:
            callback()

    def abort(self) -> None:
        with self._lock:
            if self._aborted:
                return
            self._aborted = True
            callbacks, self._callbacks = self._callbacks, []
        for cb in callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001 - closing an already closed socket is fine
                pass


@dataclass(frozen=True)
class PinnedPost:
    """One POST to vetted addresses. ``target`` is the path and query, sent byte for byte; ``host`` is the URL host,
    used for the Host header, SNI and the certificate check; the socket goes only to ``addresses`` (at most 2).

    ``repr`` leaves out the target (query tokens), the headers (signatures, custom header values) and the body, so an
    error report that captures this object never carries them (K2)."""

    scheme: str
    host: str
    port: int
    target: str = field(repr=False)
    addresses: tuple[str, ...]
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)
    deadline: float
    """Monotonic seconds: the whole attempt, from connect to the last byte read, ends here (R1)."""
    max_read: int = 4096
    abort: AbortHandle | None = None


@dataclass(frozen=True)
class TransportResult:
    status: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = field(default=b"", repr=False)
    error: str | None = None
    """``timeout``, ``connect_error``, ``tls_error`` or ``aborted`` (the lease was lost) when no answer was read."""
    connected_ip: str | None = None
    ttfb_ms: int | None = None
    detail: str | None = None
    """A short line for ``staff_detail``; never shown to a tenant."""


@runtime_checkable
class Transport(Protocol):
    def post(self, request: PinnedPost) -> TransportResult:
        """Never follows redirects, sends no proxy, cookie or forwarding header, adds Host, Content-Length and
        ``Accept-Encoding: identity``, and reads at most ``max_read`` bytes off the wire. Never raises for a network
        failure: it returns the error kind."""


@dataclass(frozen=True)
class EndpointConfig:
    """An endpoint's send configuration, decrypted at claim time. ``repr`` never shows the URL (it can carry a path
    or query token), the secrets or the custom header values (K2)."""

    id: str
    organization_id: str
    url: str = field(repr=False)
    secrets_newest_first: tuple[str, ...] = field(repr=False)
    custom_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    timeout_ms: int = 15_000
    max_age_s: int = 259_200
    status: str = "enabled"
    allowed_hosts: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """A leased delivery, ready for one attempt. ``repr`` leaves out the body; the endpoint's own ``repr`` is
    redacted."""

    delivery_id: str
    lease_token: str
    lease_until_ms: int
    event_id: str
    event_type: str
    seq: int | None
    is_test: bool
    attempt_n: int
    trigger: str
    created_at_ms: int
    body: bytes = field(repr=False)
    endpoint: EndpointConfig
    history: tuple[Mapping, ...] = ()
    """Earlier settled attempts of this delivery as ``{status, retry_after} | {error}`` + ``settled_at_ms``."""
    start_delay_ms: int = 0
    """The send start the claim reserved on the endpoint's rate (``webhooks.core.ratelimit``), as a delay from the
    claim: the sender waits this long before it connects, so starts to one endpoint keep their spacing."""
    occurred_at_ms: int | None = None
    """The event's time, for ``webhooks.delivery.first_success_seconds`` (section 13.2)."""


@runtime_checkable
class Store(Protocol):
    def fan_out(self, now_ms: int, limit: int) -> int:
        """Claim up to ``limit`` pending events whose ``not_before`` passed (SKIP LOCKED), create their deliveries
        and payload variants with :mod:`webhooks.core.fanout`, mark them done. Returns the events fanned out."""

    def reap_expired(self, now_ms: int) -> list[Claim]:
        """Deliveries whose lease expired with no settle, re-leased to the caller, so it can write their
        ``worker_lost`` attempt and settle them."""

    def claim_due(
        self,
        now_ms: int,
        lane: str,
        slots: int,
        per_org: int,
        org_busy: Mapping[str, int] | None = None,
    ) -> list[Claim]:
        """Lease due deliveries, ranked per org, at most ``slots`` in all and ``per_org`` per org counting the
        org's attempts already in flight in this loop (``org_busy``), taking a rate token and an in-flight count on
        each endpoint's state row. Every claim gets a fresh ``lease_token``."""

    def renew(self, delivery_id: str, lease_token: str, lease_until_ms: int) -> bool:
        """``UPDATE ... WHERE lease_token = %s``; False when another worker holds the delivery now."""

    def settle(self, claim: Claim, attempt: Mapping, action: Mapping) -> bool:
        """Write the attempt and apply ``action`` WHERE the lease token still matches. When it does not, write only
        the attempt, with outcome ``superseded``, and return False. Action ``block_by_policy`` writes no attempt (nothing
        was sent): the endpoint becomes ``blocked_by_policy`` and its deliveries ``paused`` (section 6.3)."""

    def release(self, claim: Claim, retry_at_ms: int, reason: str) -> bool:
        """Give back a lease that could not be attempted (no live secret, a configuration that does not decrypt):
        no attempt row, the attempt count and the in-flight count are returned, and the delivery is due again at
        ``retry_at_ms``; past its max age it ends ``failed`` with ``max_age_exceeded``. WHERE the token matches."""


@runtime_checkable
class Queue(Protocol):
    def kick(self) -> None:
        """Wake the dispatcher (``pg_notify`` in the producer's transaction on the hosted adapter)."""

    def wait(self, timeout_s: float) -> bool:
        """Block until kicked or ``timeout_s`` passed; True when kicked."""

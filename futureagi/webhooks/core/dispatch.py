"""The dispatcher loop over the ports (DESIGN.md sections 3, 7.8 and 13.1): fan out, reap expired leases as
``worker_lost``, claim what the free slots allow, send, settle under the fencing token.

The hosted adapter runs :meth:`Dispatcher.run` inside one heartbeating Temporal activity per loop; an embedded build
runs it in a thread. Senders run on a small thread pool, one per slot; the lease renewer runs while they do.

One bad row never stops the loop: a pass that raises is logged (with no frame variables, K2) and retried after a
short backoff that keeps heartbeating, the stores isolate a poisoned event or delivery, and the pool is always
drained, heartbeating, when the loop ends.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass

from . import ratelimit, schedule
from .attempt import AttemptRecord, Sender, next_action_for
from .metrics import attempt_metrics
from .ports import AbortHandle, Claim, Clock, Queue, Store
from .redact import log_exception

NORMAL, SLOW = "normal", "slow"
DEFAULT_SLOTS = {NORMAL: 48, SLOW: 16}
FANOUT_BATCH = 500
POLL_S = 1.0
BUSY_POLL_S = ratelimit.BURST_MS / 1000
"""While senders are in flight, or the last pass found work, the loop passes this often: a rate-limited endpoint keeps
its rate (each claim reserves send starts at most this far ahead, ``webhooks.core.ratelimit``), and a backlog that one
pass could not take (the per-org share caps a pass) is not left waiting a full idle poll. WT-30 found the poll-only
mode (no LISTEN) falling behind at 33 events/s for one org: senders to a fast receiver finish before the loop decides
how long to wait, so it waited the idle second and took at most the org share per second per loop."""
RENEW_EVERY_S = 20.0
UNSENDABLE_RETRY_MS = 60_000
"""A lease that cannot be attempted (no live secret, a configuration that does not decrypt) is due again after this."""
PASS_BACKOFF_MAX_S = 10.0
DRAIN_TIMEOUT_S = 45.0
"""Longer than the 30 s maximum attempt deadline plus a settle; after it the remaining sockets are closed."""
IDLE_BEAT_S = 5.0

log = logging.getLogger("webhooks.dispatch")


def per_org_share(slots: int) -> int:
    """At most max(8, slots / 4) in-flight attempts per org per loop (section 7.8); ``slots`` is the loop's total
    over both lanes, so 16 for the default 48 + 16."""
    return max(8, slots // 4)


def worker_lost_record(claim: Claim, now_ms: int) -> AttemptRecord:
    return AttemptRecord(
        n=claim.attempt_n,
        trigger=claim.trigger,
        started_at_ms=now_ms,
        settled_at_ms=now_ms,
        outcome="worker_lost",
        error_detail="Outcome unknown: the worker stopped.",
    )


def idle(
    stop: threading.Event,
    seconds: float,
    heartbeat: Callable[[], None],
    beat_every_s: float = IDLE_BEAT_S,
) -> None:
    """Wait, heartbeating, until ``stop`` is set or ``seconds`` passed. The loop runs this instead of the dispatcher
    when sending is off or not allowed on this install, so its workflow continues as new every ``seconds`` instead of
    spinning."""
    t_end = time.monotonic() + seconds
    while not stop.is_set():
        left = t_end - time.monotonic()
        if left <= 0:
            return
        heartbeat()
        stop.wait(min(beat_every_s, left))


@dataclass
class Dispatcher:
    store: Store
    sender: Sender
    clock: Clock
    queue: Queue | None = None
    scale: float = 1.0
    slots: dict | None = None
    heartbeat: Callable[[], None] = lambda: None
    after_send: Callable[[], None] = lambda: None
    """Runs on the sender thread after each send (the Django adapter releases the thread's DB connection)."""

    def __post_init__(self) -> None:
        self.slots = dict(self.slots or DEFAULT_SLOTS)
        self._busy = {NORMAL: 0, SLOW: 0}
        self._org_busy: dict[str, int] = {}
        """In-flight attempts per org in this loop, both lanes together (section 7.8)."""
        self._lock = threading.Lock()
        self._inflight: dict[str, tuple[Claim, AbortHandle]] = {}
        self._futures: set[Future] = set()
        self._pool = self._new_pool()
        self._found_work = False
        """The last pass fanned out, reaped, started or released something (there may be more)."""

    def _new_pool(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=sum(self.slots.values()), thread_name_prefix="webhooks-send"
        )

    # ---- one pass ----------------------------------------------------------------------------------------------

    def tick(self) -> dict:
        """One pass: fan-out, reap, claim and start sends. Returns counts; sends finish on the pool."""
        now = self.clock.now_ms()
        fanned = self.store.fan_out(now, FANOUT_BATCH)
        reaped = 0
        for claim in self.store.reap_expired(now):
            try:
                self.settle(claim, worker_lost_record(claim, now))
                reaped += 1
            except Exception as exc:  # noqa: BLE001 - the lease expires again; the next pass retries it
                log_exception(
                    log, "webhooks reap failed", exc, metric="webhooks.reap.failed"
                )
        started = released = 0
        share = per_org_share(sum(self.slots.values()))
        for lane in (NORMAL, SLOW):
            with self._lock:
                free = self.slots[lane] - self._busy[lane]
                org_busy = dict(self._org_busy)
            if free <= 0:
                continue
            for claim in self.store.claim_due(now, lane, free, share, org_busy):
                if not claim.endpoint.secrets_newest_first:
                    self._release(claim, now, "no_live_secret")
                    released += 1
                    continue
                self._start(lane, claim)
                started += 1
        self._found_work = bool(fanned or reaped or started or released)
        return {
            "fanned_out": fanned,
            "reaped": reaped,
            "started": started,
            "released": released,
        }

    def _start(self, lane: str, claim: Claim) -> None:
        abort = AbortHandle()
        org = claim.endpoint.organization_id
        with self._lock:
            self._busy[lane] += 1
            self._org_busy[org] = self._org_busy.get(org, 0) + 1
            self._inflight[claim.delivery_id] = (claim, abort)
            fut = self._pool.submit(self._send, claim, abort, lane)
            self._futures.add(fut)
        fut.add_done_callback(self._done(lane, claim.delivery_id, org))

    def _release(self, claim: Claim, now_ms: int, reason: str) -> None:
        """A lease that cannot be attempted: no attempt row; due again later, logged as an error with the metric
        ``webhooks.delivery.unsendable`` (never a silent loss)."""
        log.error(
            "webhooks delivery cannot be attempted",
            extra={
                "metric": "webhooks.delivery.unsendable",
                "reason": reason,
                "delivery_id": claim.delivery_id,
                "endpoint_id": claim.endpoint.id,
            },
        )
        try:
            self.store.release(claim, now_ms + UNSENDABLE_RETRY_MS, reason)
        except Exception as exc:  # noqa: BLE001 - the lease expires and is reaped
            log_exception(
                log, "webhooks release failed", exc, metric="webhooks.release.failed"
            )

    def _done(self, lane: str, delivery_id: str, org: str) -> Callable[[Future], None]:
        def cb(fut: Future) -> None:
            with self._lock:
                self._busy[lane] -= 1
                left = self._org_busy.get(org, 1) - 1
                if left > 0:
                    self._org_busy[org] = left
                else:
                    self._org_busy.pop(org, None)
                self._inflight.pop(delivery_id, None)
                self._futures.discard(fut)

        return cb

    def _send(
        self, claim: Claim, abort: AbortHandle | None = None, lane: str = NORMAL
    ) -> bool:
        try:
            self._wait_for_start(claim, abort)
            rec = self.sender.attempt(claim, abort)
            won = self.settle(claim, rec)
            attempt_metrics(claim, rec, lane, won)
            return won
        except Exception as exc:  # noqa: BLE001
            # The lease stays; when it expires the next claimer writes worker_lost and retries the delivery. The
            # frames below this one hold the decrypted configuration: they are cleared before the report (K2).
            log_exception(
                log,
                "webhooks send failed",
                exc,
                metric="webhooks.send.crashed",
                delivery_id=claim.delivery_id,
            )
            return False
        finally:
            try:
                self.after_send()
            except Exception as exc:  # noqa: BLE001
                log_exception(log, "webhooks after_send hook failed", exc)

    def _wait_for_start(self, claim: Claim, abort: AbortHandle | None) -> None:
        """Wait for the start the claim reserved on the endpoint's rate (at most ``BURST_MS``); a lost lease ends the
        wait at once, and the attempt then aborts as it would mid-send."""
        if claim.start_delay_ms <= 0:
            return
        t_end = time.monotonic() + claim.start_delay_ms / 1000
        while (left := t_end - time.monotonic()) > 0:
            if abort is not None and abort.aborted:
                return
            time.sleep(min(0.05, left))

    def settle(self, claim: Claim, rec: AttemptRecord) -> bool:
        """Apply the schedule and settle WHERE the lease token matches; False when superseded."""
        action = next_action_for(claim, rec, scale=self.scale)
        return self.store.settle(claim, rec.__dict__, action)

    def renew_leases(self) -> int:
        """Renew every in-flight lease. A renewal that touches no row means another worker owns the delivery now:
        the sender's socket is closed at once, and its settle is written as ``superseded`` (R2)."""
        now = self.clock.now_ms()
        with self._lock:
            inflight = list(self._inflight.values())
        lost = 0
        for c, abort in inflight:
            until = now + schedule.lease_ms(c.endpoint.timeout_ms)
            if not self.store.renew(c.delivery_id, c.lease_token, until):
                abort.abort()
                lost += 1
                log.warning(
                    "webhooks lease lost",
                    extra={"metric": "webhooks.lease.superseded", "alert": "WA8"},
                )
        return lost

    def busy(self) -> dict:
        with self._lock:
            return dict(self._busy)

    def _has_inflight(self) -> bool:
        with self._lock:
            return bool(self._inflight)

    def _next_wait_s(self) -> float:
        """``BUSY_POLL_S`` while senders are in flight or the last pass found work; ``POLL_S`` when idle. A queue's
        kick (LISTEN) ends either wait early."""
        return BUSY_POLL_S if self._found_work or self._has_inflight() else POLL_S

    # ---- the loop ----------------------------------------------------------------------------------------------

    def run(self, stop: threading.Event, run_for_s: float | None = None) -> None:
        """Loop until ``stop`` is set or ``run_for_s`` passed (the activity then returns and the workflow continues
        as new). Wakes on the queue's kick, or after :meth:`_next_wait_s`. A pass that raises is retried after 1, 2, 4 … 10 s,
        heartbeating; the pool is drained however the loop ends."""
        t_end = None if run_for_s is None else self.clock.monotonic() + run_for_s
        last_renew = self.clock.monotonic()
        failures = 0
        try:
            while not stop.is_set():
                ok = self._guarded("pass", self.tick)
                if self.clock.monotonic() - last_renew >= RENEW_EVERY_S:
                    self._guarded("renew", self.renew_leases)
                    last_renew = self.clock.monotonic()
                self.heartbeat()
                failures = 0 if ok else failures + 1
                if failures:
                    self._backoff(stop, min(PASS_BACKOFF_MAX_S, 2.0 ** (failures - 1)))
                if t_end is not None and self.clock.monotonic() >= t_end:
                    break
                if failures:
                    continue
                wait_s = self._next_wait_s()
                if self.queue is not None:
                    self.queue.wait(wait_s)
                else:
                    stop.wait(wait_s)
        finally:
            self.drain()

    def _guarded(self, what: str, fn: Callable[[], object]) -> bool:
        try:
            fn()
            return True
        except Exception as exc:  # noqa: BLE001 - the loop keeps going; the alert says it is failing
            log_exception(
                log,
                f"webhooks dispatcher {what} failed",
                exc,
                metric=f"webhooks.dispatcher.{what}_failed",
            )
            return False

    def _backoff(self, stop: threading.Event, seconds: float) -> None:
        t_end = time.monotonic() + seconds
        while not stop.is_set():
            left = t_end - time.monotonic()
            if left <= 0:
                return
            stop.wait(min(1.0, left))
            self._beat_quietly()

    def _beat_quietly(self) -> None:
        try:
            self.heartbeat()
        except Exception:  # noqa: BLE001 - a heartbeat failure must not strand the senders
            log.warning("webhooks heartbeat failed", exc_info=False)

    def drain(self, timeout_s: float = DRAIN_TIMEOUT_S) -> int:
        """Wait for the senders in flight, heartbeating every second, for at most ``timeout_s``; then close the
        sockets still open (their attempts settle as ``worker_lost``) and give them 5 s more. Returns how many were
        still running at the end. The dispatcher can tick again afterwards."""
        with self._lock:
            pool, self._pool = self._pool, self._new_pool()
            pending = set(self._futures)
        pool.shutdown(wait=False, cancel_futures=False)
        t_end = time.monotonic() + max(0.0, timeout_s)
        while pending:
            left = t_end - time.monotonic()
            if left <= 0:
                break
            _done, pending = wait_futures(
                pending, timeout=min(1.0, left), return_when=FIRST_COMPLETED
            )
            self._beat_quietly()
        if pending:
            with self._lock:
                handles = [abort for _c, abort in self._inflight.values()]
            for abort in handles:
                abort.abort()
            log.warning(
                "webhooks drain timed out; open sockets closed",
                extra={"metric": "webhooks.drain.timeout", "left": len(pending)},
            )
            _done, pending = wait_futures(pending, timeout=5.0)
        return len(pending)

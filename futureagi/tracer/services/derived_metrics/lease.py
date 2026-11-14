"""Project leases for metric writers; ownership checks never replace publication CAS."""

from contextlib import contextmanager
from datetime import timedelta
from time import monotonic

from django.utils import timezone

from tracer.models.derived_metric import MetricFencingError, MetricProjectRefresh

DEFAULT_LEASE_TTL = timedelta(minutes=5)


class MetricPassDeadline(BaseException):
    """Cooperative cancellation, not a read error or a failed publication.

    Like asyncio cancellation, this must pass through query-error normalization
    and generic retry handlers unchanged. The reservation boundary consumes it
    after transports/transactions unwind; PG alone schedules continuation.
    """


class MetricPassYield(MetricPassDeadline):
    """A pass stopped at its soft budget, with its attempt already aborted.

    Raised while the lease is still valid (RefreshWriter.yield_if_late): the
    attempt's ledger is carried to the next pass, never left open for a
    takeover, and nothing partial is published.
    """


class ProjectLease:
    def __init__(self, grant, *, clock=timezone.now, deadline=None, ticker=monotonic):
        self.grant = grant
        self.clock = clock
        self.expires_at = grant.expires_at
        self.deadline = deadline
        # The monotonic clock ``deadline`` is on (a fake in tests).
        self.ticker = ticker

    def budget_seconds(self):
        """Seconds left before the pass deadline, or None without one."""
        return None if self.deadline is None else self.deadline - self.ticker()

    def remaining_seconds(self):
        remaining = (self.expires_at - self.clock()).total_seconds()
        if self.deadline is not None:
            budget = self.deadline - self.ticker()
            if budget <= 0:
                raise MetricPassDeadline()
            remaining = min(remaining, budget)
        if remaining <= 0:
            raise MetricFencingError()
        return remaining

    @property
    def project_id(self):
        return self.grant.project_id

    def fence(self):
        return {
            "epoch": self.grant.fencing_epoch,
            "token": self.grant.lease_token,
            "now": self.clock(),
        }

    def check(self):
        self.remaining_seconds()
        fence = self.fence()
        if not MetricProjectRefresh.objects.filter(
            MetricProjectRefresh.lease_q(**fence), project_id=self.project_id
        ).exists():
            raise MetricFencingError()
        return fence

    def renew(self, ttl=DEFAULT_LEASE_TTL):
        self.remaining_seconds()
        if self.deadline is not None:
            ttl = min(ttl, timedelta(seconds=self.deadline - self.ticker()))
            if ttl <= timedelta(0):
                raise MetricPassDeadline()
        self.expires_at = MetricProjectRefresh.renew_lease(
            self.project_id, ttl=ttl, **self.fence()
        )
        return self.expires_at

    def keepalive(self):
        self.check()
        if self.expires_at - self.clock() < timedelta(minutes=1):
            self.renew()

    def release(self):
        from tracer.services.derived_metrics.deletion_protocol import try_control_update

        # An expired budget must not wait on a reader's refresh-row lock while
        # retaining the independent fleet slot. The fenced lease expires safely
        # if this nonblocking cleanup cannot acquire its row.
        try_control_update(
            MetricProjectRefresh.objects.filter(
                project_id=self.project_id,
                fencing_epoch=self.grant.fencing_epoch,
                lease_token=self.grant.lease_token,
            ),
            lease_token=None,
            lease_expires_at=None,
            lease_holder="",
            updated_at=self.clock(),
        )


@contextmanager
def project_lease(project_id, *, holder, ttl=DEFAULT_LEASE_TTL, clock=timezone.now):
    grant = MetricProjectRefresh.take_lease(
        project_id, holder=holder, ttl=ttl, now=clock()
    )
    if grant is None:
        raise MetricFencingError("project refresh already leased")
    lease = ProjectLease(grant, clock=clock)
    from tracer.services.derived_metrics.deletion_protocol import lifecycle_lock_budget

    try:
        with lifecycle_lock_budget(lease):
            yield lease
    finally:
        lease.release()

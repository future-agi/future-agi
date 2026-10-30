"""Project leases for metric writers; ownership checks never replace publication CAS."""

from contextlib import contextmanager
from datetime import timedelta

from django.utils import timezone

from tracer.models.derived_metric import MetricFencingError, MetricProjectRefresh

DEFAULT_LEASE_TTL = timedelta(minutes=5)


class ProjectLease:
    def __init__(self, grant, *, clock=timezone.now):
        self.grant = grant
        self.clock = clock
        self.expires_at = grant.expires_at

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
        fence = self.fence()
        if not MetricProjectRefresh.objects.filter(
            MetricProjectRefresh.lease_q(**fence), project_id=self.project_id
        ).exists():
            raise MetricFencingError()
        return fence

    def renew(self, ttl=DEFAULT_LEASE_TTL):
        self.expires_at = MetricProjectRefresh.renew_lease(
            self.project_id, ttl=ttl, **self.fence()
        )
        return self.expires_at

    def keepalive(self):
        self.check()
        if self.expires_at - self.clock() < timedelta(minutes=1):
            self.renew()

    def release(self):
        MetricProjectRefresh.release_lease(self.project_id, **self.fence())


@contextmanager
def project_lease(project_id, *, holder, ttl=DEFAULT_LEASE_TTL, clock=timezone.now):
    grant = MetricProjectRefresh.take_lease(
        project_id, holder=holder, ttl=ttl, now=clock()
    )
    if grant is None:
        raise MetricFencingError("project refresh already leased")
    lease = ProjectLease(grant, clock=clock)
    try:
        yield lease
    finally:
        lease.release()

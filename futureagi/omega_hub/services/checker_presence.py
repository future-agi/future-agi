"""Whether a Hub checker is running.

Every authenticated checker call records when that checker was last seen (api/base.py
CheckerView). An idle checker polls `claim` every 5 seconds; a busy one calls `heartbeat`
every 5 minutes inside its 15-minute lease, so a busy checker counts as alive through the
lease it holds. The submission sentences and the operations metric read this module.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from omega_hub.models import CheckerPresence, CheckJob

# One write per checker at most this often, so the 5-second idle poll mostly only reads.
WRITE_EVERY = timedelta(seconds=30)
# A checker that called within this long is alive.
ALIVE_WITHIN = timedelta(seconds=90)


def record_seen(checker_id: str, *, now=None) -> None:
    """Record that `checker_id` made an authenticated call. A call within WRITE_EVERY of
    the recorded time writes nothing; a later one moves it with a conditional update."""
    now = now or timezone.now()
    stale = now - WRITE_EVERY
    rows = CheckerPresence.objects.filter(checker_id=checker_id)
    seen = rows.values_list("last_seen_at", flat=True).first()
    if seen is None:
        CheckerPresence.objects.get_or_create(
            checker_id=checker_id, defaults={"last_seen_at": now}
        )
    elif seen < stale:
        rows.filter(last_seen_at__lt=stale).update(last_seen_at=now)


def last_seen():
    """When any checker last called the Hub, or None when none ever has."""
    return CheckerPresence.objects.aggregate(last=Max("last_seen_at"))["last"]


def alive(*, now=None) -> bool:
    """A checker is alive when any checker called within ALIVE_WITHIN, or a check job is
    RUNNING under a lease that has not ended."""
    now = now or timezone.now()
    seen = last_seen()
    if seen is not None and seen >= now - ALIVE_WITHIN:
        return True
    running = CheckJob.objects.filter(status=CheckJob.Status.RUNNING)
    return running.filter(lease_expires_at__gte=now).exists()

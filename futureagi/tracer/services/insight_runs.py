"""Reserve one active Insights run per saved report."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tracer.models.insights import InsightConfig, InsightRun

# No workflow may outlive this window (tfc.temporal.insights.client.RUN_TIMEOUT).
ACTIVE_RUN_WINDOW = timedelta(hours=2)


@dataclass(frozen=True)
class RunReservation:
    config: InsightConfig
    run: InsightRun
    created: bool


def active_report_run(config: InsightConfig) -> InsightRun | None:
    return (
        InsightRun.objects.filter(
            config=config,
            status__in=("pending", "running"),
            created_at__gte=timezone.now() - ACTIVE_RUN_WINDOW,
        )
        .order_by("-created_at")
        .first()
    )


def reserve_report_run(
    config: InsightConfig, create: Callable[[InsightConfig], InsightRun]
) -> RunReservation:
    """Recheck under the config lock before creating. ``create`` writes only
    database rows: engine resolution and workflow startup stay outside the
    reservation so another request never waits on a provider or Temporal."""
    with transaction.atomic():
        locked = InsightConfig.objects.select_for_update(of=("self",)).get(id=config.id)
        active = active_report_run(locked)
        if active is not None:
            return RunReservation(locked, active, False)
        return RunReservation(locked, create(locked), True)

"""The plan's monitor limit, asked once for a link's two default alerts (deployment or agent).

One concern: may this organization create the default monitors now? Observe ->
Alerts asks the same entitlement before each monitor it creates
(``tracer/views/monitor.py``, ``create``); ensure asks it once for the pair, so a
link gets both defaults or neither and ``alerts_created_at`` keeps meaning "the
pair is set up".

- Self-hosted (OSS or EE) is uncapped: ``check_ee_can_create`` returns at once.
- On Cloud the org's live monitors in every workspace, plus the pair, must fit
  the plan (``Entitlements.can_create`` allows one more while
  ``current_count < limit``, so room for ``needed`` is asked as
  ``current + needed - 1``).
- A refusal, or an entitlement service that cannot answer, defers the defaults:
  the project and link are still made (the collector makes the same project on
  the first trace anyway), nothing is created, and the next ensure asks again,
  after an upgrade or once monitors are deleted. The message says which of the
  three it is and what frees room: at the limit, how many monitors to delete in
  any of the org's workspaces (the count is every live monitor of the org, muted
  ones included, while Observe -> Alerts lists one workspace), or only an upgrade
  when the plan's number is below the pair's; a plan without monitors, an
  upgrade; a plan that could not be read, nothing (the next ensure retries).
  Logging and counting the deferral is ``monitors.py``'s, where the link is
  known.
- A transaction-scoped advisory lock per organization serialises the ensures that
  count, so two at once cannot both see room for the last pair. Observe -> Alerts
  does not take it; a race between the two paths is the same as between two
  Alerts creates today.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from django.db import connection

from tfc.ee_gating import EEResource, FeatureUnavailable, check_ee_can_create
from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor

_LOCK_NAMESPACE = "omega-deployment-default-monitors:"
# ``Entitlements.can_create``'s code for "the plan's number is used up".
AT_LIMIT = "ENTITLEMENT_LIMIT"


@dataclass(frozen=True)
class AlertsDeferred:
    """Why the default alerts were not created on this call; the next ensure retries."""

    code: str
    message: str
    needed: int
    limit: int | None
    current_usage: int
    upgrade_cta: dict[str, Any] | None


def _lock_org(organization_id: str) -> None:
    """Hold, until the transaction ends, the one lock per org that every counting ensure takes."""
    digest = hashlib.sha256(f"{_LOCK_NAMESPACE}{organization_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _plan_limit(refusal: FeatureUnavailable) -> int | None:
    """The plan's number when the refusal is the limit itself; ``None`` for any other refusal.

    ``CheckResult.limit`` defaults to 0, so only ``ENTITLEMENT_LIMIT`` carries a real one.
    """
    raw = refusal.metadata.get("limit")
    if refusal.error_code != AT_LIMIT or raw is None:
        return None
    return int(raw)


def _message(
    refusal: FeatureUnavailable, current: int, needed: int, message_prefix: str
) -> str:
    """Why the defaults wait, and what frees room for them.

    ``check_ee_can_create`` puts ``limit`` in the refusal's metadata only when the plan
    answered, so its absence means the entitlement service could not be read.
    ``message_prefix`` picks the deployment's or the Console agent's wording.
    """
    limit = _plan_limit(refusal)
    if limit is not None and limit < needed:
        # No number of deletions makes room for the pair; only an upgrade does.
        return get_error_message(f"{message_prefix}_ALERTS_PLAN_TOO_SMALL").format(
            needed=needed, limit=limit
        )
    if limit is not None:
        return get_error_message(f"{message_prefix}_ALERTS_AT_LIMIT").format(
            needed=needed,
            limit=limit,
            current=current,
            to_free=max(current + needed - limit, 1),
        )
    if "limit" in refusal.metadata:
        return get_error_message(f"{message_prefix}_ALERTS_NOT_ON_PLAN")
    return get_error_message(f"{message_prefix}_ALERTS_PLAN_UNAVAILABLE")


def defaults_deferred(
    organization_id: str,
    needed: int,
    *,
    message_prefix: str = "OMEGA_DEPLOYMENT_OBSERVE",
) -> AlertsDeferred | None:
    """``None`` when ``needed`` more monitors fit the org's plan, else why they do not."""
    _lock_org(organization_id)
    current = UserAlertMonitor.no_workspace_objects.filter(
        organization_id=organization_id
    ).count()
    try:
        check_ee_can_create(
            EEResource.MONITORS,
            org_id=organization_id,
            current_count=current + needed - 1,
        )
    except FeatureUnavailable as refusal:
        return AlertsDeferred(
            code=refusal.error_code,
            message=_message(refusal, current, needed, message_prefix),
            needed=needed,
            limit=_plan_limit(refusal),
            current_usage=current,
            upgrade_cta=refusal.upgrade_cta,
        )
    return None

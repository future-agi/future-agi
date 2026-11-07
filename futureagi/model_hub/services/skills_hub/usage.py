"""Best-effort fetch accounting, distinct from proof that an agent used a skill."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillUsageDaily, HubSkillVersion
from model_hub.selectors.skills_hub import scoped_skill
from model_hub.services.skills_hub.errors import SkillsHubError

logger = logging.getLogger(__name__)
_CHANNELS = ("api", "download", "mcp")


def record_fetch(
    *, version: HubSkillVersion, channel: str, consumer_key: str, consumer_label: str
) -> None:
    """Increment a UTC daily bucket; telemetry failure never breaks a fetch."""
    try:
        # A savepoint isolates even a failed database statement from a caller's
        # surrounding transaction. Telemetry deliberately fails open.
        with transaction.atomic():
            if channel not in _CHANNELS:
                raise ValueError("Unknown Skills fetch channel")
            skill = scoped_skill(version.skill)
            if not skill.versions.filter(pk=version.pk).exists():
                return
            now = timezone.now()
            lookup = {
                "skill": skill,
                "version": version,
                "day": now.date(),
                "channel": channel,
                "consumer_key": consumer_key,
            }
            rows = HubSkillUsageDaily.objects.filter(**lookup)
            if rows.update(
                count=F("count") + 1, last_at=now, consumer_label=consumer_label
            ):
                return
            try:
                with transaction.atomic():
                    HubSkillUsageDaily.objects.create(
                        **lookup, consumer_label=consumer_label, count=1, last_at=now
                    )
            except IntegrityError:
                # Another reader created the same daily bucket after our miss.
                if not rows.update(
                    count=F("count") + 1, last_at=now, consumer_label=consumer_label
                ):
                    raise
    except Exception:
        logger.exception("Could not record a Skills fetch")


def usage_summary(skill: HubSkill, days: int) -> dict:
    """Aggregate the selected UTC calendar window in SQL, scoped to one skill."""
    skill = scoped_skill(skill)
    if days not in (7, 30, 90):
        raise SkillsHubError("invalid_selector", "Choose 7, 30 or 90 days.", status=400)
    today = timezone.now().date()
    rows = skill.usage_days.filter(
        day__gte=today - timedelta(days=days - 1), day__lte=today
    )
    totals = rows.aggregate(total=Sum("count"), last_fetched_at=Max("last_at"))
    by_day = list(
        rows.values("day", version_number=F("version__number"))
        .annotate(count=Sum("count"))
        .order_by("day", "version_number")
    )
    by_version = list(
        rows.values(version_number=F("version__number"))
        .annotate(count=Sum("count"), last_at=Max("last_at"))
        .order_by("version_number")
    )
    for row in by_day + by_version:
        row["version"] = row.pop("version_number")
    by_channel = dict.fromkeys(_CHANNELS, 0)
    by_channel.update(
        {
            row["channel"]: row["total"]
            for row in rows.values("channel").annotate(total=Sum("count"))
        }
    )
    consumers = list(
        rows.values("consumer_key", "channel")
        .annotate(
            label=Max("consumer_label"), count=Sum("count"), last_at=Max("last_at")
        )
        .order_by("-count", "consumer_key", "channel")
    )
    for row in consumers:
        row.pop("consumer_key")
    return {
        "days": days,
        "total": totals["total"] or 0,
        "by_day": by_day,
        "by_channel": by_channel,
        "by_version": by_version,
        "consumers": consumers,
        "last_fetched_at": totals["last_fetched_at"],
    }


def fetches_7d_map(skill_ids: list[UUID]) -> dict[UUID, int]:
    """Batch counts for IDs already selected in an authenticated tenant scope."""
    if not skill_ids:
        return {}
    # There is intentionally no ambient workspace context: IDs are the scoped
    # selector's output. Recheck live identities and their tenant consistency.
    skills = HubSkill.no_workspace_objects.filter(
        pk__in=skill_ids, organization_id=F("workspace__organization_id")
    )
    today = timezone.now().date()
    rows = (
        HubSkillUsageDaily.objects.filter(
            skill__in=skills, day__gte=today - timedelta(days=6), day__lte=today
        )
        .values("skill_id")
        .annotate(total=Sum("count"))
    )
    result = dict.fromkeys(skills.values_list("id", flat=True), 0)
    result.update({row["skill_id"]: row["total"] for row in rows})
    return result

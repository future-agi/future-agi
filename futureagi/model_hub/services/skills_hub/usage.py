"""Best-effort fetch accounting, distinct from proof that an agent used a skill."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Greatest
from django.utils import timezone
from packaging.version import InvalidVersion, Version

from model_hub.models.skills_hub import HubSkill, HubSkillUsageDaily, HubSkillVersion
from model_hub.models.skills_hub_v12 import HubSkillContextResolution
from model_hub.selectors.skills_hub import scoped_skill
from model_hub.selectors.skills_hub_v12 import resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import SkillScope
from model_hub.services.skills_hub.v12.types import (
    ContextCredentialPrincipal,
    ResourceKind,
)

logger = logging.getLogger(__name__)
_CHANNELS = ("api", "download", "mcp", "sdk")
_TRACING_MODES = ("on", "load_only", "off", "no_otel")
_SDK_AGENT = re.compile(r"futureagi-(python|typescript)/([0-9][0-9A-Za-z.+-]{0,31})")

# Owner release decision: these are the first tracing-capable SDK releases.
# Keep the SDK family: the Python and TypeScript version lines are independent.
MIN_TRACING_SDK = {"python": "0.8.0", "typescript": "0.3.0"}


def _sdk_version(value: str | None) -> str | None:
    if not isinstance(value, str) or len(value) > 32:
        return None
    try:
        Version(value)
    except InvalidVersion:
        return None
    return value


def fetch_metadata(headers) -> dict:
    """Keep only the bounded version and the allowlisted mode, never raw headers."""
    agent = headers.get("User-Agent", "")
    match = _SDK_AGENT.fullmatch(agent) if isinstance(agent, str) else None
    mode = headers.get("X-FI-Skills-Tracing")
    version = _sdk_version(match[2]) if match else None
    return {
        "sdk_version": version,
        "sdk_language": match[1] if version else None,
        "tracing_mode": mode if mode in _TRACING_MODES else None,
    }


def can_record(
    sdk_version: str | None,
    tracing_mode: str | None,
    sdk_language: str | None = None,
) -> bool:
    """Capability hint only: neither a fetch nor a header proves a runtime load.

    Historical buckets without a language stay unknown, not implicitly Python.
    """
    version = _sdk_version(sdk_version)
    minimum = MIN_TRACING_SDK.get(sdk_language)
    if version is None or minimum is None or tracing_mode not in ("on", "load_only"):
        return False
    return Version(version) >= Version(minimum)


def _scoped_resource(resource: HubSkill) -> HubSkill:
    # Preserve the skill selector/error contract; contexts share the immutable
    # version/usage backbone but need the explicit-kind selector.
    if resource.kind == HubSkill.Kind.SKILL:
        return scoped_skill(resource)
    result = (
        resources_for_scope(
            scope=SkillScope(resource.organization_id, resource.workspace_id),
            kind=resource.kind,
        )
        .filter(pk=resource.pk)
        .first()
    )
    if result is None:
        raise SkillsHubError("resource_not_found", "Resource not found.", status=404)
    return result


def record_fetch(
    *,
    version: HubSkillVersion,
    channel: str,
    consumer_key: str,
    consumer_label: str,
    sdk_version: str | None = None,
    tracing_mode: str | None = None,
    sdk_language: str | None = None,
) -> None:
    """Increment a UTC daily bucket; telemetry failure never breaks a fetch."""
    try:
        # A savepoint isolates even a failed database statement from a caller's
        # surrounding transaction. Telemetry deliberately fails open.
        with transaction.atomic():
            if channel not in _CHANNELS:
                raise ValueError("Unknown Skills fetch channel")
            skill = _scoped_resource(version.skill)
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
            metadata = {
                "consumer_label": consumer_label,
                "sdk_version": _sdk_version(sdk_version),
                "sdk_language": (
                    sdk_language
                    if sdk_language in MIN_TRACING_SDK and _sdk_version(sdk_version)
                    else None
                ),
                "tracing_mode": (
                    tracing_mode if tracing_mode in _TRACING_MODES else None
                ),
            }
            rows = HubSkillUsageDaily.objects.filter(**lookup)
            updates = {
                "count": F("count") + 1,
                "last_at": Greatest(F("last_at"), now),
                **metadata,
            }
            if rows.update(**updates):
                return
            try:
                with transaction.atomic():
                    HubSkillUsageDaily.objects.create(
                        **lookup, **metadata, count=1, last_at=now
                    )
            except IntegrityError:
                # Another reader created the same daily bucket after our miss.
                if not rows.update(**updates):
                    raise
    except Exception:
        logger.exception("Could not record a Skills fetch")


def record_consumption_fetch(
    *,
    principal: ContextCredentialPrincipal,
    kind: ResourceKind,
    resolution_id: str,
    headers,
) -> None:
    """Account only for a successful resolve, not its manifest/file continuations."""
    from simulate.models.hosted_harness import HostedHarnessJob

    try:
        with transaction.atomic():
            scope = principal.scope
            # Native runs retain their disposable credential IDs in the server's
            # control record before dispatch. Never trust a caller header or a
            # user-editable name to distinguish those runs from application fetches.
            native_credential = Q()
            for variant in ("candidate", "production", "reference"):
                native_credential |= Q(
                    payload__contains={
                        "skills_native_control": {
                            "arms": {
                                variant: {"credential_id": str(principal.credential_id)}
                            }
                        }
                    }
                )
            if HostedHarnessJob._base_manager.filter(
                native_credential,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
            ).exists():
                return
            resolution = (
                HubSkillContextResolution.objects.filter(
                    pk=resolution_id,
                    credential_id=principal.credential_id,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    resource_kind=kind,
                    version__skill__kind=kind,
                    version__skill__organization_id=scope.organization_id,
                    version__skill__workspace_id=scope.workspace_id,
                    credential__organization_id=scope.organization_id,
                    credential__workspace_id=scope.workspace_id,
                )
                .select_related("version__skill", "credential")
                .first()
            )
            if resolution is None:
                return
            # The foundation credential model has no name field. Keep its label
            # blank until that facade supplies one; never substitute an email,
            # token, resource name, or arbitrary request header.
            label = getattr(resolution.credential, "name", "") or ""
            record_fetch(
                version=resolution.version,
                channel="sdk",
                consumer_key=f"credential:{principal.credential_id}",
                consumer_label=label[:200],
                **fetch_metadata(headers),
            )
    except Exception:
        logger.exception("Could not record a Skills consumption fetch")


def _consumer_rows(rows):
    # Metadata must come from the most recent bucket, not MAX(version/mode/label)
    # independently: those can invent a combination that no consumer ever sent.
    latest = rows.filter(
        skill_id=OuterRef("skill_id"),
        consumer_key=OuterRef("consumer_key"),
        channel=OuterRef("channel"),
    ).order_by("-last_at", "-day", "-id")
    consumers = list(
        rows.values("skill_id", "consumer_key", "channel")
        .annotate(count=Sum("count"), last_at=Max("last_at"))
        .annotate(
            label=Subquery(latest.values("consumer_label")[:1]),
            sdk_version=Subquery(latest.values("sdk_version")[:1]),
            sdk_language=Subquery(latest.values("sdk_language")[:1]),
            tracing_mode=Subquery(latest.values("tracing_mode")[:1]),
        )
        .order_by("-count", "consumer_key", "channel", "skill_id")
    )
    for row in consumers:
        row.pop("consumer_key")
        row["can_record"] = can_record(
            row["sdk_version"],
            row["tracing_mode"],
            row.pop("sdk_language"),
        )
    return consumers


def usage_summary(skill: HubSkill, days: int) -> dict:
    """Aggregate the selected UTC calendar window for either resource kind."""
    skill = _scoped_resource(skill)
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
    # Preserve the legacy empty/API-only shape; add sdk when that channel exists.
    by_channel = dict.fromkeys(("api", "download", "mcp"), 0)
    by_channel.update(
        {
            row["channel"]: row["total"]
            for row in rows.values("channel").annotate(total=Sum("count"))
        }
    )
    consumers = _consumer_rows(rows)
    for row in consumers:
        row.pop("skill_id")
    first = skill.usage_days.aggregate(first=Min("day"))["first"]
    return {
        "days": days,
        "total": totals["total"] or 0,
        "by_day": by_day,
        "by_channel": by_channel,
        "by_version": by_version,
        "consumers": consumers,
        "last_fetched_at": totals["last_fetched_at"],
        "first_fetched_on": first,
    }


def _live_resources(skill_ids: list[UUID]):
    # IDs must already be selected in an authenticated tenant scope. Recheck live
    # identities and their tenant consistency, without ambient workspace filters.
    return HubSkill.no_workspace_objects.filter(
        pk__in=skill_ids,
        organization_id=F("workspace__organization_id"),
        kind__in=("skill", "agent_context"),
    )


def consumers_7d(skill_ids: list[UUID]) -> dict[UUID, list[dict]]:
    """Batch latest consumer diagnostics for already-authorized resource IDs.

    Values have the same shape as usage_summary's consumers. This is not an
    authorization entry point: callers must never pass IDs straight from a query.
    """
    if not skill_ids:
        return {}
    skills = _live_resources(skill_ids)
    result = {pk: [] for pk in skills.values_list("id", flat=True)}
    today = timezone.now().date()
    rows = HubSkillUsageDaily.objects.filter(
        skill_id__in=result, day__gte=today - timedelta(days=6), day__lte=today
    )
    for row in _consumer_rows(rows):
        result[row.pop("skill_id")].append(row)
    return result


def fetches_7d_map(skill_ids: list[UUID]) -> dict[UUID, int]:
    """Batch counts for IDs already selected in an authenticated tenant scope."""
    if not skill_ids:
        return {}
    skills = _live_resources(skill_ids)
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

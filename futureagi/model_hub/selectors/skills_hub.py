"""Explicit tenant boundaries for every Skills lookup."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkill, HubSkillUsageDaily, HubSkillVersion
from model_hub.services.skills_hub.errors import SkillsHubError


def skills_for_request_scope(
    organization: Organization | None, workspace: Workspace | None
) -> QuerySet[HubSkill]:
    """Return live skills in the authenticated, explicitly resolved tenant."""
    if organization is None or workspace is None:
        raise SkillsHubError(
            "workspace_required", "Select a workspace to use Skills.", status=403
        )
    return HubSkill.no_workspace_objects.filter(
        organization_id=organization.pk,
        workspace_id=workspace.pk,
        workspace__organization_id=organization.pk,
    )


def get_skill_for_scope(
    organization: Organization | None, workspace: Workspace | None, skill_id: UUID
) -> HubSkill:
    """Fetch an identity without revealing objects in another tenant."""
    try:
        return skills_for_request_scope(organization, workspace).select_related(
            "created_by", "updated_by"
        ).get(pk=skill_id)
    except HubSkill.DoesNotExist as exc:
        raise SkillsHubError("skill_not_found", "Skill not found.", status=404) from exc


def scoped_skill(skill: HubSkill, *, for_update: bool = False) -> HubSkill:
    """Recheck a caller's tenant-scoped object; optionally serialize its writes."""
    queryset = HubSkill.no_workspace_objects.filter(
        organization_id=skill.organization_id,
        workspace_id=skill.workspace_id,
        workspace__organization_id=skill.organization_id,
    )
    if for_update:
        queryset = queryset.select_for_update(of=("self",))
    try:
        return queryset.get(pk=skill.pk)
    except HubSkill.DoesNotExist as exc:
        raise SkillsHubError("skill_not_found", "Skill not found.", status=404) from exc


def version_for_skill(skill: HubSkill, number: int) -> HubSkillVersion:
    """Reach a version only through the supplied live, tenant-scoped skill."""
    skill = scoped_skill(skill)
    try:
        return skill.versions.get(number=number)
    except HubSkillVersion.DoesNotExist as exc:
        raise SkillsHubError("version_not_found", "Version not found.", status=404) from exc


def list_skills(
    organization: Organization | None,
    workspace: Workspace | None,
    *,
    search: str = "",
    tag: str = "",
    label: str = "",
    sort: str = "updated",
    order: str = "desc",
) -> QuerySet[HubSkill]:
    """Filter the library; pagination stays at the API boundary."""
    queryset = skills_for_request_scope(organization, workspace)
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(description__icontains=search)
            | Q(tags__icontains=search)
        )
    if tag:
        queryset = queryset.filter(tags__contains=[tag])
    if label == "latest":
        queryset = queryset.filter(latest_version_number__gt=0)
    elif label:
        queryset = queryset.filter(labels__name=label)
    fields = {"updated": "updated_at", "name": "name", "created": "created_at"}
    if sort not in fields or order not in {"asc", "desc"}:
        raise SkillsHubError("invalid_selector", "Choose a valid sort order.", status=400)
    ordering = ("-" if order == "desc" else "") + fields[sort]
    return queryset.select_related("created_by", "updated_by").prefetch_related(
        "labels__version", "labels__updated_by", "drafts__user"
    ).order_by(ordering, "id")


def workspace_stats(
    organization: Organization | None, workspace: Workspace | None, user: User | None
) -> dict[str, int]:
    """Workspace-wide metrics, independent of library search and paging."""
    skills = skills_for_request_scope(organization, workspace)
    counts = skills.aggregate(
        total=Count("id", distinct=True),
        in_production=Count("id", filter=Q(labels__name="production"), distinct=True),
        my_drafts=Count("drafts", filter=Q(drafts__user=user), distinct=True),
    )
    since = timezone.now().date() - timedelta(days=6)
    counts["fetches_7d"] = HubSkillUsageDaily.objects.filter(
        skill__in=skills, day__gte=since
    ).aggregate(total=Sum("count"))["total"] or 0
    return counts

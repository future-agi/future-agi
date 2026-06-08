from dataclasses import dataclass

from accounts.models.workspace import WorkspaceMembership


@dataclass(frozen=True)
class LifecycleCandidate:
    user: object
    organization: object
    workspace: object


def lifecycle_candidates(*, limit=100, user_id=None, workspace_id=None):
    queryset = (
        WorkspaceMembership.no_workspace_objects.select_related(
            "user",
            "workspace",
            "workspace__organization",
        )
        .filter(
            is_active=True,
            user__is_active=True,
            workspace__is_active=True,
        )
        .order_by("created_at")
    )
    if user_id:
        queryset = queryset.filter(user_id=user_id)
    if workspace_id:
        queryset = queryset.filter(workspace_id=workspace_id)

    candidates = []
    seen = set()
    for membership in queryset[: max(limit * 3, limit)]:
        key = (membership.user_id, membership.workspace_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            LifecycleCandidate(
                user=membership.user,
                organization=membership.workspace.organization,
                workspace=membership.workspace,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates

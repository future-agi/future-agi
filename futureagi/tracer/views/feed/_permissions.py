"""
Shared helpers for the error-feed views: accessible-project resolution and
the Enterprise license gate.
"""

from typing import List, Optional

from tracer.models.project import Project


class ErrorFeedLicenseRequired:
    """Mixin denying error-feed APIs without the error_feed entitlement.

    Error feed is an Enterprise feature: the code is public but
    use requires a valid EE license (or a cloud plan). Raises
    FeatureUnavailable (HTTP 402) via check_ee_feature otherwise.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        from tfc.ee_gating import EEFeature, check_ee_feature

        org = getattr(request, "organization", None)
        check_ee_feature(EEFeature.ERROR_FEED, org_id=str(org.id) if org else None)


def get_accessible_project_ids(request) -> List[str]:
    """
    Return list of project IDs the request's user has access to.

    Scoped by organization (+ workspace if present on the request).
    Returns empty list if the user has no organization.
    """
    org = getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )
    if not org:
        return []

    qs = Project.objects.filter(organization_id=org.id)
    workspace = getattr(request, "workspace", None)
    if workspace is not None:
        qs = qs.filter(workspace_id=workspace.id)
    return [str(pid) for pid in qs.values_list("id", flat=True)]


def resolve_requested_project_ids(
    request, requested_project_id: Optional[str]
) -> Optional[List[str]]:
    """
    Given an optional `project_id` filter, return the effective list of project
    IDs to query, or None if the user is forbidden from the requested project.

    - If requested_project_id is None → return all accessible project IDs.
    - If requested_project_id is set and accessible → return [requested_project_id].
    - If requested_project_id is set but NOT accessible → return None (403).
    """
    accessible = get_accessible_project_ids(request)
    if requested_project_id is None:
        return accessible
    if str(requested_project_id) in accessible:
        return [str(requested_project_id)]
    return None

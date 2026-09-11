from django.db.models import Q


def run_test_workspace_filter(request, relation_prefix=""):
    """Return a Q object limiting a RunTest relation to the active workspace."""
    workspace = getattr(request, "workspace", None)
    if workspace is None:
        return Q()

    prefix = f"{relation_prefix}__" if relation_prefix else ""
    workspace_query = Q(**{f"{prefix}workspace": workspace})
    if getattr(workspace, "is_default", False):
        workspace_query |= Q(
            **{
                f"{prefix}workspace__is_default": True,
                f"{prefix}workspace__organization_id": workspace.organization_id,
            }
        ) | Q(**{f"{prefix}workspace__isnull": True})

    return workspace_query

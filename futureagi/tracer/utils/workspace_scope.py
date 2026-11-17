from django.db.models import Q

from tfc.utils.tenant_scope import caller_organization
from tracer.models.project import Project


def get_request_organization(request):
    """The caller's organization (``tfc.utils.tenant_scope.caller_organization``)."""
    return caller_organization(request)


# Matches no row: Django compiles an empty ``pk__in`` to an always-false
# condition, inside an AND or an OR.
_NO_ROWS = Q(pk__in=[])


def project_workspace_scope_q(request, project_prefix="project__"):
    organization = get_request_organization(request)
    if organization is None:
        # No organization: no project is the caller's. Said explicitly rather
        # than left to a ``project__organization=None`` lookup that matches
        # nothing only because the column is not nullable.
        return _NO_ROWS
    scope = Q(**{f"{project_prefix}organization": organization})

    workspace = getattr(request, "workspace", None) or getattr(
        getattr(request, "user", None), "workspace", None
    )
    if workspace:
        if getattr(workspace, "is_default", False):
            scope &= (
                Q(**{f"{project_prefix}workspace": workspace})
                | Q(
                    **{
                        f"{project_prefix}workspace__is_default": True,
                        f"{project_prefix}workspace__organization": organization,
                    }
                )
                | Q(**{f"{project_prefix}workspace__isnull": True})
            )
        else:
            scope &= Q(**{f"{project_prefix}workspace": workspace})

    return scope


def project_queryset_for_request(request):
    manager = getattr(Project, "no_workspace_objects", Project.objects)
    return manager.filter(
        project_workspace_scope_q(request, project_prefix=""),
        deleted=False,
    )

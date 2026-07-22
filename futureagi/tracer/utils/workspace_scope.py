from django.db.models import Q

from tracer.models.project import Project


def get_request_organization(request):
    return getattr(request, "organization", None) or getattr(
        getattr(request, "user", None), "organization", None
    )


def project_workspace_scope_q(request, project_prefix="project__"):
    organization = get_request_organization(request)
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

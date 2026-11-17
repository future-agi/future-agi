"""Fresh request-local querysets for writable serializer relations."""

from django.db.models import Q

from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.tenant_scope import (
    caller_organization,
    scope_to_caller,
    set_related_queryset,
)


def scope_relations(serializer, fields):
    """Map each field to its model's organization/workspace lookup paths.

    Rebuild from the unbound manager: a class-level queryset may have captured
    ambient context at import time. Missing request context grants no rows.
    """
    request = serializer.context.get("request")
    for name, (organization_field, workspace_field) in fields.items():
        field = serializer.fields[name]
        child = getattr(field, "child_relation", field)
        model = child.queryset.model
        manager = getattr(model, "no_workspace_objects", model._default_manager)
        rows = manager.filter(deleted=False)
        if request is None:
            rows = rows.none()
        else:
            rows = scope_to_caller(
                rows,
                request,
                organization_field=organization_field,
                workspace_field=workspace_field,
            )
        set_related_queryset(field, rows)


def eval_template_queryset(request):
    """System templates and the caller's active workspace templates."""
    organization = caller_organization(request) if request is not None else None
    if organization is None:
        return EvalTemplate.no_workspace_objects.none()
    workspace_templates = scope_to_caller(
        EvalTemplate.no_workspace_objects.all(), request
    )
    return EvalTemplate.no_workspace_objects.filter(
        Q(organization__isnull=True) | Q(pk__in=workspace_templates.values("pk")),
        deleted=False,
    )

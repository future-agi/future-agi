"""Fail-closed data scope for an authenticated request without a landing.

An absent background context remains different from an authenticated empty
scope. These helpers do not choose a workspace or grant access.
"""

from functools import cache

from django.db.models import Q

from tfc.middleware.workspace_context import workspace_scope_is_empty


def empty_workspace_filter():
    """Deny a missing authenticated scope, including in related-field selectors."""
    return Q(pk__in=[]) if workspace_scope_is_empty() else Q()


@cache
def model_has_workspace_data(model):
    """Recognize direct and FK-owned workspace data, without querying rows.

    Column/Row/Cell and similar child tables need the same empty-scope guard as
    their Dataset or Project. Follow only concrete forward relations, never
    reverse relations (an organization is not workspace-owned). Memberships
    are authorization metadata: the canonical access check must still be able
    to read them when resolving or replacing an empty scope.
    """
    if model._meta.label_lower == "accounts.workspacemembership":
        return False

    pending = [model]
    visited = set()
    while pending:
        candidate = pending.pop()
        if candidate in visited:
            continue
        visited.add(candidate)
        for field in candidate._meta.fields:
            if field.name == "workspace":
                return True
            if field.is_relation and field.related_model is not None:
                pending.append(field.related_model)
    return False

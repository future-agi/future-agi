"""Querysets filtered by the caller's organization and workspace.

A queryset written in a class body (a view's ``queryset`` attribute, or a
related field's ``queryset=`` argument) is built once, when its module is
imported, before any request exists, so it has no per-request filter. These
helpers build the queryset for each request instead:

- views: in ``get_queryset``;
- serializers: in ``__init__``, from ``self.context["request"]``, with
  :func:`set_related_queryset` (a ``many=True`` field validates every id
  through its ``child_relation``, so that is the queryset that has to change).

An id that the per-request queryset does not contain then fails like an id
that does not exist.

These helpers resolve the caller's organization with :func:`caller_organization`.
The separate organization resolvers in ``model_hub.utils.workspace_scope`` and
``tracer.utils.workspace_scope`` do not delegate here; they still fall back to
``request.user.organization`` when ``request.organization`` is absent or None.
:func:`align_legacy_organization` is currently unused.
"""

from django.db.models import Model, Q

from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_workspace,
)

_UNSET = object()


def has_membership_rows(user, organization=None):
    """Whether ``user`` has an organization membership row, in any state.

    Inactive and removed (soft-deleted) rows count, so only an account with no
    row at all is older than memberships. With ``organization``, only rows in
    that organization count.
    """
    from accounts.models.organization_membership import OrganizationMembership

    # The base manager applies no soft-delete filter and no workspace filter.
    rows = OrganizationMembership._base_manager.filter(user=user)
    if organization is not None:
        rows = rows.filter(organization_id=getattr(organization, "pk", organization))
    return rows.exists()


def user_organization(user):
    """The organization authentication would resolve for ``user`` by default.

    An active membership is the source of truth. The user's legacy
    ``organization`` field counts only while the user still has an active
    membership in that organization, or has no membership rows at all, in any
    state (an account older than memberships), as authentication rules. A
    user whose membership rows are all inactive or removed has no
    organization.

    A stand-in that is not a stored account offers only its ``organization``
    attribute.
    """
    from accounts.models.organization_membership import OrganizationMembership

    if not isinstance(user, Model) or user.pk is None:
        return getattr(user, "organization", None)
    memberships = OrganizationMembership.no_workspace_objects.filter(user=user)
    legacy_id = getattr(user, "organization_id", None)
    if legacy_id is not None and (
        memberships.filter(organization_id=legacy_id, is_active=True).exists()
        or not has_membership_rows(user)
    ):
        return user.organization
    membership = (
        memberships.filter(is_active=True).select_related("organization").first()
    )
    return membership.organization if membership is not None else None


def acts_in_organization(user, organization):
    """Whether ``user`` may act in ``organization``.

    True for an active membership in it, and for an account older than
    memberships (no membership rows at all, in any state) whose legacy
    ``organization`` field names it, as authentication rules. An inactive or
    removed membership grants nothing.
    """
    from accounts.models.organization_membership import OrganizationMembership

    if user is None or organization is None:
        return False
    organization_id = getattr(organization, "pk", organization)
    if OrganizationMembership.no_workspace_objects.filter(
        user=user, organization_id=organization_id, is_active=True
    ).exists():
        return True
    return (
        getattr(user, "organization_id", None) is not None
        and str(user.organization_id) == str(organization_id)
        and not has_membership_rows(user)
    )


def account_belongs_to_organization(user, organization):
    """Administrative target ownership, including a still-pending invitation.

    An invitation does not authorize its invitee to act; it only remains
    manageable by the organization that issued it.
    """
    if acts_in_organization(user, organization):
        return True
    if user is None or organization is None or user.is_active or not user.invited_by_id:
        return False
    from accounts.models.organization_membership import OrganizationMembership

    organization_id = getattr(organization, "pk", organization)
    return (
        str(user.organization_id) == str(organization_id)
        and OrganizationMembership.no_workspace_objects.filter(
            user=user,
            organization_id=organization_id,
            is_active=False,
            invited_by_id=user.invited_by_id,
            deleted=False,
        ).exists()
    )


def align_legacy_organization(user, organization):
    """Make ``user.organization`` agree with the resolved ``organization``.

    When the account's legacy ``organization`` field names an organization in
    which it has no active membership (an administrator deactivated it), set
    the field on this in-memory object to the organization authentication
    resolved, or to ``None`` when none resolved. Code that reads
    ``request.user.organization`` then sees the same organization as
    ``request.organization``. Nothing is saved; an account that is still an
    active member of its legacy organization keeps it.
    """
    if user is None or not isinstance(user, Model) or user.pk is None:
        return
    legacy_id = getattr(user, "organization_id", None)
    if legacy_id is None:
        return
    if organization is not None and str(organization.pk) == str(legacy_id):
        return
    if acts_in_organization(user, legacy_id):
        return
    user.organization = organization


def caller_organization(request=None):
    """The caller's organization.

    - A request that authentication has seen: ``request.organization``, as
      authentication set it, ``None`` included; nothing else is read.
    - A request it has not seen (a test's ``force_authenticate``, a request
      handled outside DRF) with a user who is not anonymous:
      :func:`user_organization`.
    - Otherwise (no request, as in a serializer validated without one, or an
      anonymous user): the bound workspace context, which authentication sets
      for every request it authenticates.
    """
    if request is not None:
        organization = getattr(request, "organization", _UNSET)
        if organization is not _UNSET:
            return organization
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", True):
            return user_organization(user)
    return get_current_organization()


def caller_workspace(request=None):
    """The caller's workspace: the request's, then the bound one, else None."""
    if request is not None:
        workspace = getattr(request, "workspace", None)
        if workspace is not None:
            return workspace
    return get_current_workspace()


def workspace_scope_q(workspace, field="workspace"):
    """Rows whose ``field`` lies in ``workspace``, by the manager's rule.

    This is the rule ``BaseModelManager`` applies: a default workspace also
    covers the organization's other default workspaces and rows with no
    workspace; any other workspace covers only itself. The rows with no
    workspace are limited to the caller's organization by the organization
    filter that :func:`scope_to_caller` always adds. No workspace narrows
    nothing.
    """
    if workspace is None:
        return Q()
    if getattr(workspace, "is_default", False):
        scope = Q(**{field: workspace})
        organization_id = getattr(workspace, "organization_id", None)
        if organization_id is not None:
            scope |= Q(
                **{
                    f"{field}__is_default": True,
                    f"{field}__organization_id": organization_id,
                }
            )
        return scope | Q(**{f"{field}__isnull": True})
    return Q(**{field: workspace})


def scope_to_caller(
    queryset,
    request=None,
    *,
    organization_field="organization",
    workspace_field="workspace",
):
    """Narrow ``queryset`` to the caller's organization and workspace.

    ``organization_field`` and ``workspace_field`` are lookups from the
    queryset's model (``"dataset__organization"`` for a model that belongs to
    a dataset). Pass ``workspace_field=None`` for a model with no workspace on
    its path. No organization means no rows.
    """
    organization = caller_organization(request)
    if organization is None:
        return queryset.none()
    queryset = queryset.filter(**{organization_field: organization})
    if workspace_field:
        queryset = queryset.filter(
            workspace_scope_q(caller_workspace(request), workspace_field)
        )
    return queryset


def set_related_queryset(field, queryset):
    """Make a related serializer field resolve ids against ``queryset``.

    For a ``many=True`` field DRF builds a ``ManyRelatedField`` whose
    ``child_relation`` resolves each id; its own ``queryset`` attribute is
    never read, so both are set.
    """
    child = getattr(field, "child_relation", None)
    if child is not None:
        child.queryset = queryset
    field.queryset = queryset

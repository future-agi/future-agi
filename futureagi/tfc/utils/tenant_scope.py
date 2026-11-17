"""Querysets scoped to the caller's organization and workspace.

A queryset written in a class body (a view's ``queryset`` attribute, or a
related field's ``queryset=`` argument) is built once, when its module is
imported. That happens before any caller is known, so such a queryset carries
no tenant filter. Build the queryset for each caller instead, from the request:

- views: in ``get_queryset``;
- serializers: in ``__init__``, from ``self.context["request"]``, with
  :func:`set_related_queryset` (a ``many=True`` field validates every id
  through its ``child_relation``, so that is the queryset that has to change).

An id outside the caller's scope then fails exactly like an id that does not
exist.

This module is the one place that decides who the caller's organization is;
``model_hub.utils.workspace_scope`` and ``tracer.utils.workspace_scope``
delegate to it.
"""

from django.db.models import Model, Q

from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
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


def _stored_account(user):
    """Whether ``user`` is an account loaded from, or saved to, the database.

    A model instance built in memory and never saved is a stand-in, even when
    its primary key is already set: ``User.id`` is a UUID with a default, so
    ``User(email=...)`` has a pk before any save. Only a stored account has
    membership rows to consult, so only a stored account costs a query.
    """
    return isinstance(user, Model) and user.pk is not None and not user._state.adding


def user_organization(user):
    """The organization authentication would resolve for ``user`` by default.

    An active membership is the source of truth. The user's legacy
    ``organization`` field counts only while the user still has an active
    membership in that organization, or has no membership rows at all, in any
    state (an account older than memberships), as authentication rules. A
    user whose membership rows are all inactive or removed has no
    organization.

    A stand-in that is not a stored account (see :func:`_stored_account`)
    offers only its ``organization`` attribute, and reads no database.
    """
    from accounts.models.organization_membership import OrganizationMembership

    if not _stored_account(user):
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

    True when the account acts in the organization
    (:func:`acts_in_organization`), and for a pending invitation the
    organization itself issued: an account not yet activated, whose legacy
    ``organization`` field names the organization, with a live inactive
    membership row there from the same inviter. An invitation does not
    authorize its invitee to act; it only remains manageable by the
    organization that issued it. A removed member, a cancelled (soft-deleted)
    invitation or another inviter's row does not count. A stand-in that is not
    a stored account (see :func:`_stored_account`) never counts, and reads no
    database.
    """
    if not _stored_account(user):
        return False
    if acts_in_organization(user, organization):
        return True
    if user is None or organization is None or user.is_active:
        return False
    from accounts.models.organization_invite import InviteStatus, OrganizationInvite
    from accounts.models.organization_membership import OrganizationMembership

    organization_id = getattr(organization, "pk", organization)
    if (
        OrganizationMembership._base_manager.filter(
            user=user, organization_id=organization_id, is_active=False, deleted=False
        ).exists()
        and OrganizationInvite.objects.filter(
            organization_id=organization_id,
            target_email__iexact=user.email,
            status=InviteStatus.PENDING,
        ).exists()
    ):
        return True
    return (
        bool(user.invited_by_id)
        and str(user.organization_id) == str(organization_id)
        and OrganizationMembership.no_workspace_objects.filter(
            user=user,
            organization_id=organization_id,
            is_active=False,
            invited_by_id=user.invited_by_id,
            deleted=False,
        ).exists()
    )


def counts_as_member(user, organization):
    """Whether ``user`` counts as a member of ``organization`` when an
    administrator of it removes, deletes, renames or re-invites the account.

    The same rule as :func:`account_belongs_to_organization` (the stricter
    form from the dataset tenancy hotfix): an active membership, an account
    older than memberships, or a live pending invitation the organization
    issued.
    """
    return account_belongs_to_organization(user, organization)


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
    if not _stored_account(user):
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
      authentication set it, ``None`` included. No other source can widen it.
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
    a dataset). Pass ``workspace_field=None`` only for an organization-level
    operation or a model with no workspace on its path. No organization means
    no rows; workspace-scoped operations also require a live, accessible
    workspace. Recheck access here because authentication's workspace object
    and membership decision can become stale before this queryset is built.
    """
    organization = caller_organization(request)
    if organization is None:
        return queryset.none()
    if workspace_field is not None:
        from accounts.models.workspace import Workspace

        # An explicit empty request scope cannot borrow an ambient workspace.
        workspace = getattr(request, "workspace", _UNSET)
        if workspace is _UNSET:
            workspace = caller_workspace(request)
        user = (
            getattr(request, "user", None)
            if request is not None
            else get_current_user()
        )
        if workspace is None or not getattr(user, "is_authenticated", False):
            return queryset.none()
        workspace = (
            Workspace.no_workspace_objects.filter(
                pk=workspace.pk, organization=organization, is_active=True
            )
            .select_related("organization")
            .first()
        )
        # This reads live org/workspace memberships and retains Owner/Admin
        # global access, rather than duplicating their authorization policy.
        if workspace is None or not user.can_access_workspace(workspace):
            return queryset.none()
        queryset = queryset.filter(workspace_scope_q(workspace, workspace_field))
    return queryset.filter(**{organization_field: organization})


def scope_to_accessible_workspace(
    queryset,
    organization,
    workspace,
    *,
    user=None,
    organization_field="organization",
    workspace_field="workspace",
):
    """Fail-closed adapter for services receiving scope values, not a request.

    The actor is explicit when supplied, otherwise the authenticated bound
    user. Organization and workspace are always explicit, including None;
    neither may borrow ambient scope. Authorization stays in scope_to_caller.
    """
    from types import SimpleNamespace

    return scope_to_caller(
        queryset,
        SimpleNamespace(
            organization=organization,
            workspace=workspace,
            user=user if user is not None else get_current_user(),
        ),
        organization_field=organization_field,
        workspace_field=workspace_field,
    )


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

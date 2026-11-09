"""A new organization's default workspace, provisioned by the write that creates it.

Authentication and workspace landing only read (``workspace_landing``): they
never create a workspace or grant access. So every write that creates an
organization for a person also creates its default workspace and that
person's workspace-admin access to it, in its own transaction: sign-up
(``accounts.utils.first_signup``, which OAuth/SSO, ``create_user`` and
``bootstrap_install`` also use), ``POST /accounts/organizations/create/`` and
``POST /accounts/organizations/new/``.

It grants a workspace membership only. It issues no API key of any kind
(ruling 733) and does not touch ``user.config``: landing finds the workspace
through the organization's ``is_default`` flag.
"""

from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.hard_erase_issuance_entry import issuance_entry
from accounts.services.workspace_membership import create_workspace_membership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

DEFAULT_WORKSPACE_NAME = "Default Workspace"
DEFAULT_WORKSPACE_DESCRIPTION = "Default workspace for the organization"


def provision_default_workspace(organization, user, *, organization_membership):
    """Return ``organization``'s default workspace, administered by ``user``.

    Call it inside the transaction that creates ``organization`` and
    ``organization_membership`` (the creator's active membership).

    Idempotent: an existing active default workspace is reused, and a
    membership row the pair already has is left as it is. A removed
    membership is never brought back; the pair is unique across removed rows
    too, so no second row is created next to it.

    Fails closed: an inactive or removed default workspace still holds the
    organization's single default (``unique_default_workspace_per_org``), so
    creating a new one raises ``IntegrityError`` instead of reactivating it.
    """
    with issuance_entry(organizations=[organization.pk], people=[user.pk]):
        workspace = Workspace.no_workspace_objects.filter(
            organization=organization, is_default=True, is_active=True
        ).first()
        if workspace is None:
            workspace = Workspace.objects.create(
                name=DEFAULT_WORKSPACE_NAME,
                display_name=DEFAULT_WORKSPACE_NAME,
                description=DEFAULT_WORKSPACE_DESCRIPTION,
                organization=organization,
                is_default=True,
                is_active=True,
                created_by=user,
            )
        if not WorkspaceMembership._base_manager.filter(
            workspace=workspace, user=user
        ).exists():
            create_workspace_membership(
                workspace=workspace,
                user=user,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                level=Level.WORKSPACE_ADMIN,
                organization_membership=organization_membership,
            )
        return workspace

"""Live person and workspace checks shared by grant issuers and eval admission."""

from tfc.routers import primary_required
from tfc.service_envelope import EnvelopeRejected

from accounts.api_key_policy import live_membership, membership_role
from accounts.models import Organization, User
from accounts.models.workspace import Workspace, WorkspaceMembership


@primary_required
def live_person(org_id, user_id, workspace_id="-"):
    membership = live_membership(user_id, org_id)
    user = User._base_manager.filter(pk=user_id, is_active=True).first()
    org = Organization.objects.filter(pk=org_id).first()
    if membership is None or user is None or org is None:
        raise EnvelopeRejected()
    workspace = None
    if workspace_id and workspace_id != "-":
        workspace = Workspace._base_manager.filter(
            pk=workspace_id, organization_id=org_id, is_active=True, deleted=False
        ).first()
        if (
            workspace is None
            or not WorkspaceMembership._base_manager.filter(
                user_id=user_id,
                workspace_id=workspace_id,
                is_active=True,
                deleted=False,
            ).exists()
        ):
            raise EnvelopeRejected()
    user.organization = org
    user.organization_role = membership_role(membership)
    return user, org, workspace

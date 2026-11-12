"""Synthetic Django setup and read-only state snapshots; imported after OS/59l bootstrap."""
import secrets


def create_people():
    from accounts.authentication import generate_encrypted_message
    from accounts.models.auth_token import AuthToken, AuthTokenType
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from django.utils import timezone
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    people = {}
    for tag in ("A", "B"):
        org = Organization.objects.create(name="catalogue-733-" + secrets.token_hex(8))
        workspace = None
        for label, role, level in (("owner", OrganizationRoles.OWNER, Level.OWNER), ("member", OrganizationRoles.MEMBER, Level.MEMBER)):
            user = User.objects.create_user(email="catalogue-" + secrets.token_hex(8) + "@example.test", password=secrets.token_urlsafe(24), name="Synthetic catalogue " + label, organization=org, organization_role=role)
            membership = OrganizationMembership.no_workspace_objects.create(user=user, organization=org, role=role, level=level, is_active=True)
            if workspace is None:
                workspace = Workspace.no_workspace_objects.create(name="Catalogue fixture", organization=org, is_default=True, is_active=True, created_by=user)
            WorkspaceMembership.no_workspace_objects.create(user=user, workspace=workspace, role="Workspace Owner" if label == "owner" else OrganizationRoles.WORKSPACE_MEMBER, level=Level.OWNER if label == "owner" else Level.WORKSPACE_MEMBER, organization_membership=membership, is_active=True)
            access = AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS.value, is_active=True, last_used_at=timezone.now())
            token = generate_encrypted_message({"user_id": str(user.id), "id": str(access.id)})
            people[tag + "-" + label] = {"org": str(org.pk), "workspace": str(workspace.pk), "headers": {"Authorization": "Bearer " + token, "X-Organization-Id": str(org.pk), "X-Workspace-Id": str(workspace.pk)}}
    return people


def database_snapshot(own_org, digest):
    # _base_manager includes revoked/deleted rows and every config version.
    # Hash ALL fields, including encrypted vault bytes, without logging values.
    from agentcc.models.mcp_credential import AgentccMCPCredential
    from agentcc.models.org_config import AgentccOrgConfig
    groups = {"own": {}, "foreign": {}}
    for model in (AgentccMCPCredential, AgentccOrgConfig):
        rows = list(model._base_manager.order_by("pk").values())
        for group in groups:
            selected = [row for row in rows if (str(row["organization_id"]) == own_org) == (group == "own")]
            groups[group][model.__name__] = {"count": len(selected), "digest": digest(selected)}
    return groups

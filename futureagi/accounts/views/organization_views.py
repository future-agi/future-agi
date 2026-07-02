import structlog
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AdditionalOrganizationCreateResponseSerializer,
    OrganizationCreateRequestSerializer,
    OrganizationCreateResponseSerializer,
    OrganizationNameRequestSerializer,
    OrganizationUpdateRequestSerializer,
    OrganizationUpdateResponseSerializer,
)
from accounts.services.workspace_membership import create_workspace_membership
from accounts.utils import process_post_registration
from tfc.constants.email import FREE_EMAIL_DOMAINS
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class OrganizationCreateAPIView(APIView):
    """
    POST /accounts/organizations/create/

    For users who were removed from their org and want to start fresh.
    Only accessible to authenticated users with no current organization.
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=OrganizationNameRequestSerializer,
        responses={
            201: OrganizationCreateResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        gm = GeneralMethods()
        user = request.user

        # Only allow org-less users (membership is source of truth)
        has_org = OrganizationMembership.no_workspace_objects.filter(
            user=user, is_active=True
        ).exists()
        if has_org:
            return gm.bad_request("You already belong to an organization.")

        org_name = (request.validated_data.get("organization_name") or "").strip()
        if not org_name:
            # Derive from email domain (same logic as first_signup in accounts/utils.py)
            email_parts = user.email.split("@")
            domain = email_parts[1] if len(email_parts) > 1 else ""
            if domain in FREE_EMAIL_DOMAINS:
                org_name = ""
            else:
                org_name = domain.split(".")[0]

        with transaction.atomic():
            # 1. Create Organization
            organization = Organization.objects.create(name=org_name)

            # Safety net: set legacy FK so request.user.organization works
            user.organization = organization
            user.organization_role = "Owner"
            user.save(update_fields=["organization", "organization_role"])

            # 2. Create OrganizationMembership
            org_membership, _ = OrganizationMembership.objects.get_or_create(
                user=user,
                organization=organization,
                defaults={
                    "role": "Owner",
                    "level": Level.OWNER,
                    "is_active": True,
                },
            )

            # 4. Create default Workspace
            workspace = Workspace.objects.create(
                name="Default Workspace",
                display_name="Default Workspace",
                description="Default workspace for the organization",
                organization=organization,
                is_default=True,
                created_by=user,
            )

            # 5. Create WorkspaceMembership
            create_workspace_membership(
                workspace=workspace,
                user=user,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                level=Level.WORKSPACE_ADMIN,
                organization_membership=org_membership,
                manager=WorkspaceMembership.objects,
            )

            # 6. Create system API key
            OrgApiKey.no_workspace_objects.create(
                organization=organization,
                type="system",
            )

            # 7. Set user config
            if not user.config:
                user.config = {}
            user.config["currentWorkspaceId"] = str(workspace.id)
            user.config["defaultWorkspaceId"] = str(workspace.id)
            user.config["selected_organization_id"] = str(organization.id)
            user.config["currentOrganizationId"] = str(organization.id)
            user.save(update_fields=["config"])

        # 8. Trigger post-registration onboarding.
        # Password is None since the user already has an account — the signup
        # email portion will be a no-op or gracefully skipped.
        try:
            process_post_registration(str(user.id), None)
        except Exception:
            logger.exception(
                "Failed to trigger post-registration onboarding",
                user_id=str(user.id),
                organization_id=str(organization.id),
            )

        return gm.success_response(
            {
                "organization_id": str(organization.id),
                "organization_name": organization.name,
                "workspace_id": str(workspace.id),
                "message": "Organization created successfully.",
            },
            status=status.HTTP_201_CREATED,
        )


class OrganizationUpdateAPIView(APIView):
    """
    PATCH /accounts/organizations/update/

    Update the current organization's name/display_name.
    Only accessible to Owner or Admin of the organization.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=OrganizationUpdateRequestSerializer,
        responses={
            200: OrganizationUpdateResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def patch(self, request):
        org = getattr(request, "organization", None)
        if not org:
            return self._gm.bad_request("No organization context.")

        user_role = request.user.get_organization_role(org)
        if user_role not in [
            OrganizationRoles.OWNER.value,
            OrganizationRoles.ADMIN.value,
        ]:
            return self._gm.forbidden_response(
                "Only owners and admins can update organization settings."
            )

        name = request.validated_data.get("name")
        display_name = request.validated_data.get("display_name")

        if name is not None:
            name = name.strip()
            if not name:
                return self._gm.bad_request("Organization name cannot be empty.")
            org.name = name

        if display_name is not None:
            org.display_name = display_name.strip()

        org.save()

        return self._gm.success_response(
            {
                "id": str(org.id),
                "name": org.name,
                "display_name": org.display_name,
            }
        )


class CreateAdditionalOrganizationView(APIView):
    """
    POST /accounts/organizations/new/

    Create a new organization for an already-authenticated user.
    Unlike OrganizationCreateAPIView (which is for org-less users),
    this allows any user to create additional organizations.
    The user becomes Owner of the new org via OrganizationMembership.
    Does NOT change user.organization FK (primary org stays the same).
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=OrganizationCreateRequestSerializer,
        responses={
            201: AdditionalOrganizationCreateResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        gm = GeneralMethods()
        user = request.user

        org_name = (request.validated_data.get("name") or "").strip()
        display_name = (request.validated_data.get("display_name") or "").strip()

        if not org_name:
            return gm.bad_request("Organization name is required.")

        with transaction.atomic():
            # 1. Create Organization
            organization = Organization.objects.create(
                name=org_name,
                display_name=display_name or org_name,
            )

            # Safety net: set legacy FK only if user doesn't already belong to an org
            if not user.organization_id:
                user.organization = organization
                user.organization_role = OrganizationRoles.OWNER.value
                user.save(update_fields=["organization", "organization_role"])

            # 2. Create OrganizationMembership (user is Owner)
            org_membership = OrganizationMembership.no_workspace_objects.create(
                user=user,
                organization=organization,
                role=OrganizationRoles.OWNER,
                level=Level.OWNER,
                is_active=True,
            )

            # 3. Create default Workspace
            workspace = Workspace.objects.create(
                name="Default Workspace",
                display_name="Default Workspace",
                description="Default workspace for the organization",
                organization=organization,
                is_default=True,
                created_by=user,
            )

            # 4. Create WorkspaceMembership
            create_workspace_membership(
                workspace=workspace,
                user=user,
                role=OrganizationRoles.WORKSPACE_ADMIN,
                level=Level.WORKSPACE_ADMIN,
                organization_membership=org_membership,
            )

            # 5. Create system API key
            OrgApiKey.no_workspace_objects.create(
                organization=organization,
                type="system",
            )

        logger.info(
            "Additional organization created",
            user_id=str(user.id),
            organization_id=str(organization.id),
        )

        return gm.success_response(
            {
                "organization": {
                    "id": str(organization.id),
                    "name": organization.name,
                    "display_name": organization.display_name,
                },
                "workspace": {
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "display_name": workspace.display_name,
                    "is_default": workspace.is_default,
                },
                "message": "Organization created successfully.",
            },
            status=status.HTTP_201_CREATED,
        )

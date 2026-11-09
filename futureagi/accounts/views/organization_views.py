import structlog
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.authentication import allows_no_organization, allows_no_workspace
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AdditionalOrganizationCreateResponseSerializer,
    OrganizationCreateRequestSerializer,
    OrganizationCreateResponseSerializer,
    OrganizationNameRequestSerializer,
    OrganizationUpdateRequestSerializer,
    OrganizationUpdateResponseSerializer,
)
from accounts.services.default_workspace import provision_default_workspace
from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock
from accounts.services.identity_issuance import _exists, issue_membership
from accounts.utils import process_post_registration
from tfc.constants.email import FREE_EMAIL_DOMAINS
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


@allows_no_organization
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

        organization = Organization(name=org_name)
        with lifecycle_lock(
            keys=identity_keys(organizations=[organization.pk], people=[user.pk])
        ):
            _exists(User, user.pk, "default")
            organization.save(force_insert=True)

            # Safety net: set legacy FK so request.user.organization works
            user.organization = organization
            user.organization_role = "Owner"
            user.save(update_fields=["organization", "organization_role"])

            # 2. Create OrganizationMembership
            org_membership, _ = issue_membership(
                OrganizationMembership.objects,
                "get_or_create",
                user=user,
                organization=organization,
                defaults={
                    "role": "Owner",
                    "level": Level.OWNER,
                    "is_active": True,
                },
            )

            # 4-5. Default Workspace, with the creator as its admin
            workspace = provision_default_workspace(
                organization, user, organization_membership=org_membership
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

    # Creates a new organization, not data in the caller's current workspace.
    # Keep the active-organization requirement; only a workspace may be absent.
    permission_classes = [IsAuthenticated]

    @allows_no_workspace("POST")
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

        organization = Organization(
            name=org_name, display_name=display_name or org_name
        )
        with lifecycle_lock(
            keys=identity_keys(organizations=[organization.pk], people=[user.pk])
        ):
            _exists(User, user.pk, "default")
            organization.save(force_insert=True)

            # Safety net: set legacy FK only if user doesn't already belong to an org
            if not user.organization_id:
                user.organization = organization
                user.organization_role = OrganizationRoles.OWNER.value
                user.save(update_fields=["organization", "organization_role"])

            # 2. Create OrganizationMembership (user is Owner)
            org_membership = issue_membership(
                OrganizationMembership.no_workspace_objects,
                "create",
                user=user,
                organization=organization,
                role=OrganizationRoles.OWNER,
                level=Level.OWNER,
                is_active=True,
            )

            # 3-4. Default Workspace, with the creator as its admin
            workspace = provision_default_workspace(
                organization, user, organization_membership=org_membership
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

import logging
from uuid import UUID

from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    CurrentOrganizationResponseSerializer,
    OrganizationSelectionListResponseSerializer,
    OrganizationSelectResponseSerializer,
    OrganizationSwitchRequestSerializer,
    OrganizationSwitchResponseSerializer,
)
from accounts.services.workspace_membership import resolve_org_membership
from tfc.constants.roles import RoleMapping
from tfc.utils.api_contracts import validated_api_request, validated_request
from tfc.utils.general_methods import GeneralMethods

logger = logging.getLogger(__name__)


def _validate_uuid(value):
    """Validate that value is a valid UUID string."""
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


class OrganizationSelectionView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=OrganizationSwitchRequestSerializer,
        responses={
            200: OrganizationSelectResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        """Select an organization for the current session."""
        try:
            organization_id = request.validated_data["organization_id"]

            try:
                selected_org = Organization.objects.get(id=organization_id)
            except Organization.DoesNotExist:
                return self._gm.bad_request("Organization not found")

            if not request.user.can_access_organization(selected_org):
                return self._gm.forbidden_response(
                    "You don't have access to this organization"
                )

            # Update config with new key
            if not request.user.config:
                request.user.config = {}
            request.user.config["currentOrganizationId"] = str(selected_org.id)
            # Keep legacy key for backward compat
            request.user.config["selected_organization_id"] = str(selected_org.id)
            request.user.save(update_fields=["config"])

            return self._gm.success_response(
                {
                    "message": f"Successfully selected organization: {selected_org.display_name or selected_org.name}",
                    "organization": {
                        "id": str(selected_org.id),
                        "name": selected_org.name,
                        "display_name": selected_org.display_name,
                    },
                }
            )

        except Exception as e:
            logger.error(f"Failed to select organization: {str(e)}")
            return self._gm.bad_request("Failed to select organization")

    @validated_request(
        responses={
            200: OrganizationSelectionListResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        """Get all organizations the user has access to."""
        try:
            user = request.user
            # Use the org resolved by middleware (respects X-Organization-Id
            # header) so the frontend sees the correct "selected" state.
            current_org = getattr(request, "organization", None)
            current_org_id = (
                str(current_org.id)
                if current_org
                else (
                    user.config.get("currentOrganizationId")
                    or user.config.get("selected_organization_id")
                )
            )
            seen_org_ids = set()
            user_organizations = []

            # Get all active memberships (primary source of truth)
            memberships = OrganizationMembership.no_workspace_objects.filter(
                user=user, is_active=True
            ).select_related("organization")

            for membership in memberships:
                org = membership.organization
                seen_org_ids.add(org.id)
                user_organizations.append(
                    {
                        "id": str(org.id),
                        "name": org.name,
                        "display_name": org.display_name,
                        "role": membership.role,
                        "level": membership.level_or_legacy,
                        "is_selected": str(org.id) == current_org_id,
                    }
                )

            # Include legacy FK org if not in memberships (safety net)
            # But only if user actually has access to it
            if user.organization and user.organization.id not in seen_org_ids:
                org = user.organization
                # Verify user still has access (not deactivated)
                if user.can_access_organization(org):
                    user_organizations.insert(
                        0,
                        {
                            "id": str(org.id),
                            "name": org.name,
                            "display_name": org.display_name,
                            "role": user.organization_role,
                            "level": None,
                            "is_selected": str(org.id) == current_org_id,
                        },
                    )

            # Default selection: first org if nothing is selected
            if (
                not any(o["is_selected"] for o in user_organizations)
                and user_organizations
            ):
                user_organizations[0]["is_selected"] = True

            return self._gm.success_response(
                {
                    "organizations": user_organizations,
                    "total_count": len(user_organizations),
                }
            )

        except Exception as e:
            logger.error(f"Failed to get organizations: {str(e)}")
            return self._gm.bad_request("Failed to get organizations")


class SwitchOrganizationView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=OrganizationSwitchRequestSerializer,
        responses={
            200: OrganizationSwitchResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        """Switch to a different organization.

        Returns the target org's last-used workspace (from orgWorkspaceMap)
        or its default workspace, so the frontend can update both contexts.
        """
        try:
            organization_id = request.validated_data["organization_id"]

            try:
                selected_org = Organization.objects.get(id=organization_id)
            except Organization.DoesNotExist:
                return self._gm.bad_request("Organization not found")

            user = request.user

            if not user.can_access_organization(selected_org):
                return self._gm.forbidden_response(
                    "You don't have access to this organization"
                )

            # Get user's membership for role info
            membership = user.get_membership(selected_org)
            org_role = membership.role if membership else user.organization_role
            org_level = membership.level_or_legacy if membership else None

            # Resolve workspace for this org
            workspace = self._resolve_workspace_for_org(user, selected_org)

            # Get workspace role
            ws_role = user.get_workspace_role(workspace) if workspace else None

            # Update user config
            if not user.config:
                user.config = {}
            user.config["currentOrganizationId"] = str(selected_org.id)
            # Keep legacy key for backward compat
            user.config["selected_organization_id"] = str(selected_org.id)
            if workspace:
                user.config["currentWorkspaceId"] = str(workspace.id)
                org_workspace_map = user.config.get("orgWorkspaceMap", {})
                org_workspace_map[str(selected_org.id)] = str(workspace.id)
                user.config["orgWorkspaceMap"] = org_workspace_map
            user.save(update_fields=["config"])

            response_data = {
                "organization": {
                    "id": str(selected_org.id),
                    "name": selected_org.name,
                    "display_name": selected_org.display_name,
                    "ws_enabled": selected_org.ws_enabled,
                },
                "org_role": org_role,
                "org_level": org_level,
                "workspace_role": ws_role,
            }

            if workspace:
                response_data["workspace"] = {
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "display_name": workspace.display_name,
                    "is_default": workspace.is_default,
                }

            return self._gm.success_response(response_data)

        except Exception as e:
            logger.exception(f"Failed to switch organization: {str(e)}")
            return self._gm.bad_request("Failed to switch organization")

    def _resolve_workspace_for_org(self, user, organization):
        """Resolve the best workspace for the given org."""
        # 1. Check orgWorkspaceMap
        org_workspace_map = user.config.get("orgWorkspaceMap", {})
        last_ws_id = org_workspace_map.get(str(organization.id))

        if last_ws_id:
            try:
                ws = Workspace.objects.get(
                    id=last_ws_id, organization=organization, is_active=True
                )
                if user.can_access_workspace(ws):
                    return ws
            except (Workspace.DoesNotExist, ValueError):
                pass

        # 2. Org's default workspace
        try:
            ws = Workspace.objects.get(
                organization=organization, is_default=True, is_active=True
            )
            return ws
        except Workspace.DoesNotExist:
            pass

        # 3. Create default workspace
        try:
            ws = Workspace.objects.create(
                name="Default Workspace",
                organization=organization,
                is_default=True,
                is_active=True,
                created_by=user,
            )
            # Ensure workspace membership
            org_role = user.get_organization_role(organization)
            ws_role = RoleMapping.get_workspace_role(org_role or "Member")
            WorkspaceMembership.no_workspace_objects.get_or_create(
                workspace=ws,
                user=user,
                defaults={
                    "role": ws_role,
                    "invited_by": user,
                    "is_active": True,
                    "organization_membership": resolve_org_membership(
                        user, organization
                    ),
                },
            )
            return ws
        except Exception as e:
            logger.error(f"Failed to create default workspace: {e}")
            return None


@swagger_auto_schema(
    method="get",
    responses={200: CurrentOrganizationResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@validated_api_request(
    responses={200: CurrentOrganizationResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    document=False,
)
def get_current_organization(request):
    """Get the currently selected organization for the user."""
    _gm = GeneralMethods()

    try:
        user = request.user
        # Use the org resolved by authentication (already handles priority)
        if hasattr(request, "organization") and request.organization:
            selected_org = request.organization
            source = "resolved"
        else:
            # Fallback: check config
            selected_org_id = user.config.get(
                "currentOrganizationId"
            ) or user.config.get("selected_organization_id")
            selected_org = None
            source = None

            if selected_org_id and _validate_uuid(selected_org_id):
                try:
                    selected_org = Organization.objects.get(id=selected_org_id)
                    if not user.can_access_organization(selected_org):
                        selected_org = None
                    else:
                        source = "selected"
                except Organization.DoesNotExist:
                    selected_org = None

            # Fallback to primary organization
            if selected_org is None and user.organization:
                selected_org = user.organization
                source = "primary"

        if selected_org:
            membership = user.get_membership(selected_org)
            return _gm.success_response(
                {
                    "organization": {
                        "id": str(selected_org.id),
                        "name": selected_org.name,
                        "display_name": selected_org.display_name,
                        "ws_enabled": selected_org.ws_enabled,
                    },
                    "role": membership.role if membership else user.organization_role,
                    "level": membership.level_or_legacy if membership else None,
                    "source": source,
                }
            )

        return _gm.success_response(
            {"organization": None, "message": "No organization available"}
        )

    except Exception as e:
        logger.error(f"Failed to get current organization: {str(e)}")
        return _gm.bad_request("Failed to get current organization")

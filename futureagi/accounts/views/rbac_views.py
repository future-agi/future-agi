"""
New RBAC API views.

These run alongside the old endpoints during transition.
Old endpoints remain untouched until Phase 4 cutover.
"""

import json

import structlog
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.serializers.rbac import (
    InviteCancelSerializer,
    InviteCreateSerializer,
    InviteResendSerializer,
    MemberListRequestSerializer,
    MemberRemoveSerializer,
    MemberRoleUpdateSerializer,
    WorkspaceMemberListRequestSerializer,
    WorkspaceMemberRemoveSerializer,
    WorkspaceMemberRoleUpdateSerializer,
)
from accounts.utils import (
    existing_member_access_will_change,
    generate_password,
    resolve_org,
    send_invite_email,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.permissions.rbac import (
    CanManageTargetUser,
    IsOrganizationAdmin,
    IsOrganizationAdminOrWorkspaceAdmin,
)
from tfc.permissions.utils import (
    can_invite_at_level,
    get_effective_workspace_level,
    get_org_membership,
)
from tfc.utils.audit import log_audit
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


# =====================================================================
# Helpers
# =====================================================================


def _actor_manages_invite_workspaces(actor, invite):
    """Check if the actor has WS Admin access to at least one workspace in the invite."""
    ws_access = invite.workspace_access or []
    if not ws_access:
        return False
    for ws_entry in ws_access:
        ws_id = ws_entry.get("workspace_id")
        if ws_id:
            actor_ws_level = get_effective_workspace_level(actor, ws_id)
            if actor_ws_level is not None and actor_ws_level >= Level.WORKSPACE_ADMIN:
                return True
    return False


# =====================================================================
# Invite endpoints
# =====================================================================


class InviteCreateAPIView(APIView):
    """
    POST /accounts/organization/invite/

    Create invites for one or more email addresses.
    Also dual-writes to legacy User/membership records for backward compat.
    """

    permission_classes = [IsAuthenticated, IsOrganizationAdminOrWorkspaceAdmin]

    def post(self, request):
        gm = GeneralMethods()
        serializer = InviteCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        data = serializer.validated_data
        user = request.user
        organization = resolve_org(request)

        if not organization:
            return gm.bad_request(get_error_message("USER_NOT_IN_ORG"))

        actor_membership = get_org_membership(user)
        if not actor_membership:
            return gm.forbidden_response(get_error_message("NOT_ORG_MEMBER"))

        actor_level = actor_membership.level_or_legacy
        target_org_level = data["org_level"]

        # WS Admin: can only invite as org Viewer
        if actor_level < Level.ADMIN:
            target_org_level = Level.VIEWER

        # Enforce invite level rule
        if not can_invite_at_level(actor_level, target_org_level):
            return gm.forbidden_response(get_error_message("INVITE_LEVEL_FORBIDDEN"))

        workspace_access = data.get("workspace_access", [])

        # Validate workspace access levels
        for ws_entry in workspace_access:
            ws_id = ws_entry.get("workspace_id")
            # Verify workspace belongs to org
            if not Workspace.objects.filter(
                id=ws_id, organization=organization
            ).exists():
                return gm.bad_request(get_error_message("WS_NOT_IN_ORG"))

        # R1: WS Admins can only invite to workspaces they manage
        if actor_level < Level.ADMIN and workspace_access:
            for ws_entry in workspace_access:
                ws_id = ws_entry.get("workspace_id")
                actor_ws_level = get_effective_workspace_level(user, ws_id)
                if actor_ws_level is None or actor_ws_level < Level.WORKSPACE_ADMIN:
                    return gm.forbidden_response(
                        get_error_message("INVITE_WS_ACCESS_FORBIDDEN")
                    )

        # ── Validate emails upfront ──
        emails = [e.lower().strip() for e in data["emails"]]

        for email in emails:
            if email == user.email.lower():
                return gm.bad_request(get_error_message("CANNOT_INVITE_SELF"))

        created_invites = []
        already_members = []

        for email in emails:
            try:
                # For already-active org members, add them to the requested
                # workspaces without creating a new invite.
                existing_user = User.objects.filter(email=email).first()
                if existing_user and existing_user.is_active:
                    if OrganizationMembership.all_objects.filter(
                        user=existing_user,
                        organization=organization,
                        is_active=True,
                        deleted=False,
                    ).exists():
                        access_will_change = existing_member_access_will_change(
                            existing_user,
                            organization,
                            target_org_level,
                            workspace_access,
                        )
                        with transaction.atomic():
                            self._dual_write_legacy(
                                email,
                                organization,
                                user,
                                target_org_level,
                                workspace_access,
                            )
                        if access_will_change:
                            send_invite_email(email, organization, user)
                        already_members.append(email)
                        continue

                with transaction.atomic():
                    invite, created = OrganizationInvite.objects.update_or_create(
                        organization=organization,
                        target_email=email,
                        status=InviteStatus.PENDING,
                        defaults={
                            "level": target_org_level,
                            "workspace_access": workspace_access,
                            "invited_by": user,
                        },
                    )
                    invite_id = invite.id

                    self._dual_write_legacy(
                        email,
                        organization,
                        user,
                        target_org_level,
                        workspace_access,
                    )

                    existing = User.objects.filter(email=email).first()
                    if existing and existing.is_active:
                        invite.status = InviteStatus.ACCEPTED
                        invite.save(update_fields=["status"])

                    send_invite_email(email, organization, user)

                    log_audit(
                        organization=organization,
                        action="member.invited",
                        scope="organization",
                        target_id=invite_id,
                        changes={
                            "email": email,
                            "level": target_org_level,
                            "workspace_access": workspace_access,
                        },
                    )

                    created_invites.append(email)

            except IntegrityError:
                logger.error("invite_integrity_error", email=email)
            except Exception:
                logger.exception("invite_unexpected_error", email=email)

        result = {"invited": created_invites}
        if already_members:
            result["already_members"] = already_members
        return gm.success_response(result)

    def _dual_write_legacy(
        self, email, organization, inviter, org_level, workspace_access
    ):
        """
        Create/update User + OrganizationMembership + WorkspaceMembership
        using old fields so old endpoints still work during transition.
        """
        org_role_string = Level.to_org_string(org_level)

        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            # Multi-org support: users can belong to multiple organizations
            # OrganizationMembership table handles multi-org relationships

            # Use all_objects to include soft-deleted rows — BaseModel soft-delete
            # leaves the DB row (and unique constraint) intact, so objects.update_or_create
            # would hit an IntegrityError for previously-removed members.
            # E1: For first-time invites of inactive users (no existing membership),
            # keep is_active=False so they go through the invite-accept flow.
            # For re-invites (existing membership found, whether deactivated or
            # soft-deleted), always reactivate so login doesn't hit
            # requires_org_setup.
            existing_membership = OrganizationMembership.all_objects.filter(
                user=existing_user, organization=organization
            ).first()

            if existing_membership and existing_membership.is_active:
                # Already active member — only update org role if the new level
                # is higher (avoid accidental demotion when just adding to a workspace).
                update_fields = {
                    "invited_by": inviter,
                    "deleted": False,
                    "deleted_at": None,
                }
                if org_level > existing_membership.level_or_legacy:
                    update_fields["role"] = org_role_string
                    update_fields["level"] = org_level
                OrganizationMembership.all_objects.filter(
                    pk=existing_membership.pk
                ).update(**update_fields)
            else:
                # Existing inactive membership (deactivated or cancelled) →
                # always reactivate so the user can log in instead of being
                # stuck with requires_org_setup=True.
                # Brand-new membership for an inactive user → keep False so
                # they go through the invite-accept flow first.
                membership_active = (
                    True if existing_membership is not None else existing_user.is_active
                )
                OrganizationMembership.all_objects.update_or_create(
                    user=existing_user,
                    organization=organization,
                    defaults={
                        "role": org_role_string,
                        "level": org_level,
                        "invited_by": inviter,
                        "is_active": membership_active,
                        "deleted": False,
                        "deleted_at": None,
                    },
                )

            target_user = existing_user
        else:
            # Create new user
            target_user = User.objects.create_user(
                email=email,
                password=generate_password(),
                name=email.split("@")[0],
                invited_by=inviter,
                is_active=False,
                organization=organization,
            )
            target_user.invited_organizations.add(organization)
            OrganizationMembership.all_objects.update_or_create(
                user=target_user,
                organization=organization,
                defaults={
                    "role": org_role_string,
                    "level": org_level,
                    "invited_by": inviter,
                    "is_active": False,  # Stays False until invite is accepted
                    "deleted": False,
                    "deleted_at": None,
                },
            )

        # Create workspace memberships
        # Use _base_manager (plain Django Manager) to bypass both soft-delete
        # and workspace context filtering — both can cause IntegrityError on
        # re-invite after cancel (soft-deleted row not found → INSERT → unique violation).
        is_existing_active = existing_user is not None and existing_user.is_active
        org_membership = OrganizationMembership._base_manager.filter(
            deleted=False,
        ).get(
            user=target_user,
            organization=organization,
        )
        unfiltered_ws_qs = WorkspaceMembership._base_manager
        for ws_entry in workspace_access:
            ws_id = ws_entry.get("workspace_id")
            ws_level = ws_entry.get("level", Level.WORKSPACE_VIEWER)
            try:
                workspace = Workspace.objects.get(id=ws_id, organization=organization)
                ws_role = Level.to_ws_role(ws_level)
                unfiltered_ws_qs.update_or_create(
                    workspace=workspace,
                    user=target_user,
                    defaults={
                        "role": ws_role,
                        "level": ws_level,
                        "organization_membership": org_membership,
                        "invited_by": inviter,
                        "is_active": is_existing_active,
                        "deleted": False,
                        "deleted_at": None,
                    },
                )
            except Workspace.DoesNotExist:
                continue


class InviteResendAPIView(APIView):
    """
    POST /accounts/organization/invite/resend/
    Resets expiration and resends the invite email.
    """

    permission_classes = [IsAuthenticated, IsOrganizationAdminOrWorkspaceAdmin]

    def post(self, request):
        gm = GeneralMethods()
        serializer = InviteResendSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        organization = resolve_org(request)
        try:
            invite = OrganizationInvite.objects.get(
                id=serializer.validated_data["invite_id"],
                organization=organization,
                status=InviteStatus.PENDING,
            )
        except OrganizationInvite.DoesNotExist:
            return gm.bad_request(get_error_message("INVITE_NOT_FOUND"))

        # R2: WS Admins can only resend invites for workspaces they manage
        actor_membership = get_org_membership(request.user)
        if actor_membership and actor_membership.level_or_legacy < Level.ADMIN:
            if not _actor_manages_invite_workspaces(request.user, invite):
                return gm.forbidden_response(
                    get_error_message("INVITE_RESEND_WS_FORBIDDEN")
                )

        # Optionally update org level if provided — with escalation guard
        new_org_level = serializer.validated_data.get("org_level")
        if new_org_level is not None:
            actor_membership = get_org_membership(request.user)
            if not actor_membership:
                return gm.forbidden_response(get_error_message("NOT_ORG_MEMBER"))
            actor_level = actor_membership.level_or_legacy
            if not can_invite_at_level(actor_level, new_org_level):
                return gm.forbidden_response(
                    get_error_message("INVITE_LEVEL_SET_FORBIDDEN")
                )
            invite.level = new_org_level
            invite.save(update_fields=["level"])

        invite.refresh_expiration()

        send_invite_email(invite.target_email, organization, request.user)

        log_audit(
            organization=organization,
            action="invite.resent",
            scope="organization",
            target_id=invite.id,
        )

        return gm.success_response({"message": "Invite resent successfully."})


class InviteCancelAPIView(APIView):
    """
    DELETE /accounts/organization/invite/cancel/
    Hard deletes the invite record.
    """

    permission_classes = [IsAuthenticated, IsOrganizationAdminOrWorkspaceAdmin]

    def delete(self, request):
        gm = GeneralMethods()
        serializer = InviteCancelSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        organization = resolve_org(request)
        try:
            invite = OrganizationInvite.objects.get(
                id=serializer.validated_data["invite_id"],
                organization=organization,
                status=InviteStatus.PENDING,
            )
        except OrganizationInvite.DoesNotExist:
            return gm.bad_request(get_error_message("INVITE_NOT_FOUND"))

        # R2: WS Admins can only cancel invites for workspaces they manage
        actor_membership = get_org_membership(request.user)
        if actor_membership and actor_membership.level_or_legacy < Level.ADMIN:
            if not _actor_manages_invite_workspaces(request.user, invite):
                return gm.forbidden_response(
                    get_error_message("INVITE_CANCEL_WS_FORBIDDEN")
                )

        invite.status = InviteStatus.CANCELLED
        invite.save(update_fields=["status"])

        # Clean up dual-write artifacts: soft-delete the OrganizationMembership
        # and WorkspaceMembership records created during invite for users who
        # never activated their account.
        # BUG-4 fix: soft-delete (not just deactivate) so cancelled invites
        # don't linger as "Deactivated" entries in the member list.
        try:
            user = User.objects.get(email__iexact=invite.target_email)
            if not user.is_active:
                now = timezone.now()
                # Use _base_manager to bypass soft-delete and workspace filtering
                OrganizationMembership._base_manager.filter(
                    user=user,
                    organization=organization,
                ).update(is_active=False, deleted=True, deleted_at=now)
                WorkspaceMembership._base_manager.filter(
                    user=user,
                    workspace__organization=organization,
                ).update(is_active=False, deleted=True, deleted_at=now)
        except User.DoesNotExist:
            pass

        log_audit(
            organization=organization,
            action="invite.cancelled",
            scope="organization",
            target_id=invite.id,
            changes={"email": invite.target_email},
        )

        return gm.success_response({"message": "Invite cancelled."})


# =====================================================================
# Member management endpoints
# =====================================================================


class MemberListAPIView(APIView):
    """
    GET /accounts/organization/members/

    Returns UNION of active members + pending/expired invites.
    Status is derived at query time (Active / Pending / Expired).
    """

    permission_classes = [IsAuthenticated, IsOrganizationAdmin]

    def get(self, request):
        gm = GeneralMethods()

        # Pre-process query params: parse JSON-encoded list params
        query_data = request.query_params.copy()
        for list_field in (
            "filter_status",
            "filterStatus",
            "filter_role",
            "filterRole",
        ):
            raw = query_data.get(list_field)
            if raw and isinstance(raw, str) and raw.startswith("["):
                try:
                    query_data.setlist(list_field, json.loads(raw))
                except (ValueError, TypeError):
                    pass
        # Normalize camelCase → snake_case for query params
        if "filterStatus" in query_data and "filter_status" not in query_data:
            query_data.setlist("filter_status", query_data.getlist("filterStatus"))
        if "filterRole" in query_data and "filter_role" not in query_data:
            query_data.setlist("filter_role", query_data.getlist("filterRole"))

        serializer = MemberListRequestSerializer(data=query_data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        params = serializer.validated_data
        organization = resolve_org(request)

        if not organization:
            return gm.bad_request(get_error_message("USER_NOT_IN_ORG"))

        # Org-level member list always returns full workspace memberships;
        # workspace_id is NOT used here (it's only for workspace-scoped endpoints).

        # Build pending/expired invites
        invites = self._get_invites(organization)

        # Collect emails with pending invites so we can deduplicate
        invited_emails = {inv["email"] for inv in invites}

        # Build active members queryset, excluding those with pending invites
        # (dual-write creates both an OrganizationMembership and an
        # OrganizationInvite — the invite represents the true state)
        members = self._get_members(
            organization,
            workspace_id=None,
            exclude_emails=invited_emails,
        )

        # Combine into a single list
        combined = list(members) + list(invites)

        # Apply search
        search = params.get("search", "").lower()
        if search:
            combined = [
                r
                for r in combined
                if search in r.get("name", "").lower()
                or search in r.get("email", "").lower()
            ]

        # Apply status filter
        filter_status = params.get("filter_status", [])
        if filter_status:
            combined = [r for r in combined if r["status"] in filter_status]

        # Apply role filter — values may be prefixed: "org_8", "ws_3",
        # or plain integers "8" for backward compat.
        filter_role = params.get("filter_role", [])
        if filter_role:
            org_levels = set()
            ws_levels = set()
            for val in filter_role:
                val = str(val)
                if val.startswith("org_"):
                    org_levels.add(int(val[4:]))
                elif val.startswith("ws_"):
                    ws_levels.add(int(val[3:]))
                else:
                    # Plain integer — match both fields (backward compat)
                    try:
                        level = int(val)
                        org_levels.add(level)
                        ws_levels.add(level)
                    except ValueError:
                        pass
            combined = [
                r
                for r in combined
                if r.get("org_level") in org_levels or r.get("ws_level") in ws_levels
            ]

        # Sort
        ALLOWED_SORT_FIELDS = {
            "name",
            "email",
            "role",
            "level",
            "status",
            "type",
            "date_joined",
            "created_at",
        }
        sort_field = params.get("sort", "-created_at")
        reverse = sort_field.startswith("-")
        sort_key = sort_field.lstrip("-")
        if sort_key not in ALLOWED_SORT_FIELDS:
            sort_key = "name"
        combined.sort(key=lambda r: r.get(sort_key, ""), reverse=reverse)

        # Paginate
        page = params.get("page", 1)
        limit = params.get("limit", 20)
        start = (page - 1) * limit
        end = start + limit

        return gm.success_response(
            {
                "results": combined[start:end],
                "total": len(combined),
                "page": page,
                "limit": limit,
            }
        )

    def _get_members(self, organization, workspace_id=None, exclude_emails=None):
        """Return org members (active and deactivated) as dicts.

        Args:
            exclude_emails: set of emails to skip (e.g. those with pending invites
                            created by dual-write, to avoid duplicate rows).
        """
        memberships = OrganizationMembership.objects.filter(
            organization=organization,
        ).select_related("user")

        if exclude_emails:
            memberships = memberships.exclude(user__email__in=exclude_emails)

        # Prefetch workspace memberships to avoid N+1 queries
        user_ids = [m.user_id for m in memberships]
        ws_qs = WorkspaceMembership.no_workspace_objects.filter(
            user_id__in=user_ids,
            workspace__organization=organization,
            is_active=True,
        ).select_related("workspace")
        if workspace_id:
            ws_qs = ws_qs.filter(workspace_id=workspace_id)
        # Build lookup: user_id -> list of workspace memberships
        ws_by_user = {}
        for ws_mem in ws_qs:
            ws_by_user.setdefault(ws_mem.user_id, []).append(ws_mem)

        results = []
        for mem in memberships:
            user = mem.user

            # Derive status from membership active flag
            member_status = "Deactivated" if not mem.is_active else "Active"

            row = {
                "id": str(user.id),
                "name": user.name or "",
                "email": user.email,
                "org_level": mem.level_or_legacy,
                "org_role": Level.to_org_string(mem.level_or_legacy),
                "status": member_status,
                "created_at": mem.joined_at.isoformat() if mem.joined_at else "",
                "type": "member",
            }

            # Deactivated members get no workspace role
            if not mem.is_active:
                row["ws_level"] = None
                row["ws_role"] = None
                row["workspaces"] = []
            elif workspace_id:
                # Get workspace level if workspace context
                user_ws_mems = ws_by_user.get(user.id, [])
                if user_ws_mems:
                    ws_mem = user_ws_mems[0]
                    row["ws_level"] = ws_mem.level_or_legacy
                    row["ws_role"] = Level.to_ws_string(ws_mem.level_or_legacy)
                else:
                    # Org Admin+ auto-gets workspace admin
                    if mem.level_or_legacy >= Level.ADMIN:
                        row["ws_level"] = Level.WORKSPACE_ADMIN
                        row["ws_role"] = "Workspace Admin"
                    else:
                        row["ws_level"] = None
                        row["ws_role"] = None
            else:
                # No workspace context — return ALL workspace memberships
                ws_memberships = ws_by_user.get(user.id, [])

                workspaces_list = [
                    {
                        "workspace_id": str(ws_mem.workspace_id),
                        "workspace_name": ws_mem.workspace.display_name
                        or ws_mem.workspace.name,
                        "ws_level": ws_mem.level_or_legacy,
                        "ws_role": Level.to_ws_string(ws_mem.level_or_legacy),
                    }
                    for ws_mem in ws_memberships
                ]

                # Admin+ auto-gets WS Admin in all org workspaces
                if mem.level_or_legacy >= Level.ADMIN:
                    explicit_ws_ids = {
                        str(ws_mem.workspace_id) for ws_mem in ws_memberships
                    }
                    all_org_workspaces = Workspace.objects.filter(
                        organization=organization, is_active=True
                    )
                    for ws in all_org_workspaces:
                        if str(ws.id) not in explicit_ws_ids:
                            workspaces_list.append(
                                {
                                    "workspace_id": str(ws.id),
                                    "workspace_name": ws.display_name or ws.name,
                                    "ws_level": Level.WORKSPACE_ADMIN,
                                    "ws_role": "Workspace Admin",
                                    "auto_access": True,
                                }
                            )

                row["workspaces"] = workspaces_list

                # Keep ws_level/ws_role for backward compat (highest role)
                if ws_memberships:
                    best = max(ws_memberships, key=lambda m: m.level_or_legacy)
                    row["ws_level"] = best.level_or_legacy
                    row["ws_role"] = Level.to_ws_string(best.level_or_legacy)
                elif mem.level_or_legacy >= Level.ADMIN:
                    row["ws_level"] = Level.WORKSPACE_ADMIN
                    row["ws_role"] = Level.to_ws_string(Level.WORKSPACE_ADMIN)
                else:
                    default_ws = Level.get_default_ws_level(mem.level_or_legacy)
                    row["ws_level"] = default_ws
                    row["ws_role"] = Level.to_ws_string(default_ws)

            results.append(row)

        return results

    def _get_invites(self, organization):
        """Return pending/expired invites as dicts."""
        invites = OrganizationInvite.objects.filter(
            organization=organization,
            status=InviteStatus.PENDING,
        )

        # Pre-fetch all active workspaces for this org to avoid N+1 queries
        org_workspaces = {
            str(ws.id): ws
            for ws in Workspace.objects.filter(
                organization=organization, is_active=True
            )
        }

        results = []
        for inv in invites:
            # Derive workspace role from invite data
            if inv.level >= Level.ADMIN:
                # Admin+ auto-gets workspace admin (same as active members)
                ws_level = Level.WORKSPACE_ADMIN
                ws_role = Level.to_ws_string(Level.WORKSPACE_ADMIN)
            elif inv.workspace_access:
                # Use the level from the first workspace entry
                ws_level = inv.workspace_access[0].get("level", Level.WORKSPACE_VIEWER)
                ws_role = Level.to_ws_string(ws_level)
            else:
                # No explicit workspace access — derive default from org level
                ws_level = Level.get_default_ws_level(inv.level)
                ws_role = Level.to_ws_string(ws_level)

            # Build workspaces list from invite's workspace_access JSON
            invite_workspaces = []
            if inv.workspace_access:
                for ws_entry in inv.workspace_access:
                    ws_id = ws_entry.get("workspace_id")
                    ws_lvl = ws_entry.get("level", Level.WORKSPACE_MEMBER)
                    ws_obj = org_workspaces.get(str(ws_id))
                    if ws_obj:
                        invite_workspaces.append(
                            {
                                "workspace_id": str(ws_id),
                                "workspace_name": ws_obj.display_name or ws_obj.name,
                                "ws_level": ws_lvl,
                                "ws_role": Level.to_ws_string(ws_lvl),
                            }
                        )

            results.append(
                {
                    "id": str(inv.id),
                    "name": inv.target_email.split("@")[0],
                    "email": inv.target_email,
                    "org_level": inv.level,
                    "org_role": Level.to_org_string(inv.level),
                    "ws_level": ws_level,
                    "ws_role": ws_role,
                    "workspaces": invite_workspaces,
                    "status": inv.effective_status,  # "Pending" or "Expired"
                    "created_at": inv.created_at.isoformat() if inv.created_at else "",
                    "type": "invite",
                }
            )

        return results


class MemberRoleUpdateAPIView(APIView):
    """
    POST /accounts/organization/members/role/

    Update a member's org level and/or workspace level.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdminOrWorkspaceAdmin,
        CanManageTargetUser,
    ]

    def post(self, request):
        gm = GeneralMethods()
        serializer = MemberRoleUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        data = serializer.validated_data
        organization = resolve_org(request)

        if organization:
            from tfc.ee_gating import EEFeature, check_ee_feature

            check_ee_feature(EEFeature.CUSTOM_ROLES, org_id=str(organization.id))

        user_id = data["user_id"]

        try:
            target_membership = OrganizationMembership.objects.get(
                user_id=user_id,
                organization=organization,
            )
        except OrganizationMembership.DoesNotExist:
            return gm.bad_request(get_error_message("MEMBER_NOT_IN_ORG"))

        # BUG-3 fix: reject role changes for deactivated members (but allow for pending invites)
        # A pending invite has an OrganizationInvite record; a deactivated member does not.
        if not target_membership.is_active:
            target_user = User.objects.filter(id=user_id).first()
            has_pending_invite = (
                target_user
                and OrganizationInvite.objects.filter(
                    organization=organization,
                    target_email__iexact=target_user.email,
                    status=InviteStatus.PENDING,
                ).exists()
            )
            if not has_pending_invite:
                return gm.bad_request(
                    get_error_message("MEMBER_DEACTIVATED_ROLE_UPDATE")
                )

        changes = {}

        with transaction.atomic():
            # Update org level — with escalation guard
            if data.get("org_level") is not None:
                old_level = target_membership.level_or_legacy
                new_level = data["org_level"]

                actor_membership = get_org_membership(request.user)
                actor_level = (
                    actor_membership.level_or_legacy if actor_membership else 0
                )
                if not can_invite_at_level(actor_level, new_level):
                    return gm.forbidden_response(
                        get_error_message("ROLE_ASSIGN_FORBIDDEN")
                    )

                # B4 fix: Race-safe last-owner check with select_for_update
                if old_level >= Level.OWNER and new_level < Level.OWNER:
                    owner_count = (
                        OrganizationMembership.objects.select_for_update()
                        .filter(
                            organization=organization,
                            is_active=True,
                            level__gte=Level.OWNER,
                        )
                        .count()
                    )
                    legacy_owner_count = (
                        OrganizationMembership.objects.select_for_update()
                        .filter(
                            organization=organization,
                            is_active=True,
                            level__isnull=True,
                            role="Owner",
                        )
                        .count()
                    )
                    if (owner_count + legacy_owner_count) <= 1:
                        return gm.bad_request(get_error_message("LAST_OWNER_DEMOTE"))

                target_membership.level = new_level
                target_membership.role = Level.to_org_string(new_level)
                target_membership.save(update_fields=["level", "role"])
                changes["org_level"] = {"old": old_level, "new": new_level}

                if new_level >= Level.ADMIN:
                    # Promote to workspace_admin across all org workspaces
                    # for consistency with implicit global access.
                    org_workspaces = Workspace.objects.filter(organization=organization)
                    for ws in org_workspaces:
                        WorkspaceMembership._base_manager.update_or_create(
                            workspace=ws,
                            user_id=user_id,
                            defaults={
                                "level": Level.WORKSPACE_ADMIN,
                                "role": Level.to_ws_role(Level.WORKSPACE_ADMIN),
                                "organization_membership": target_membership,
                                "granted_by": request.user,
                                "is_active": True,
                                "deleted": False,
                                "deleted_at": None,
                            },
                        )
                else:
                    # Below Admin — use workspace_access to grant explicit memberships.
                    ws_access = data.get("workspace_access") or []
                    default_ws_level = (
                        Level.WORKSPACE_MEMBER
                        if new_level >= Level.MEMBER
                        else Level.WORKSPACE_VIEWER
                    )
                    for ws_entry in ws_access:
                        ws_id = ws_entry.get("workspace_id")
                        ws_level = ws_entry.get("level", default_ws_level)
                        if ws_id:
                            WorkspaceMembership._base_manager.update_or_create(
                                workspace_id=ws_id,
                                user_id=user_id,
                                defaults={
                                    "level": ws_level,
                                    "role": Level.to_ws_role(ws_level),
                                    "organization_membership": target_membership,
                                    "granted_by": request.user,
                                    "is_active": True,
                                    "deleted": False,
                                    "deleted_at": None,
                                },
                            )

                # Also update User.organization_role for backward compat
                User.objects.filter(id=user_id).update(
                    organization_role=Level.to_org_string(new_level)
                )

                # Also update OrganizationInvite if user has a pending invite
                target_user = User.objects.filter(id=user_id).first()
                if target_user:
                    OrganizationInvite.objects.filter(
                        organization=organization,
                        target_email__iexact=target_user.email,
                        status=InviteStatus.PENDING,
                    ).update(level=new_level)

            # Update ws level
            if data.get("ws_level") is not None and data.get("workspace_id"):
                # Use all_objects to bypass workspace context filtering and
                # include soft-deleted rows — the DB unique constraint on
                # (workspace_id, user_id) still holds for deleted rows.
                existing_ws = WorkspaceMembership.all_objects.filter(
                    workspace_id=data["workspace_id"],
                    user_id=user_id,
                ).first()
                old_ws = existing_ws.level_or_legacy if existing_ws else None

                WorkspaceMembership.all_objects.update_or_create(
                    workspace_id=data["workspace_id"],
                    user_id=user_id,
                    defaults={
                        "level": data["ws_level"],
                        "role": Level.to_ws_role(data["ws_level"]),
                        "organization_membership": target_membership,
                        "granted_by": request.user,
                        "is_active": True,
                        "deleted": False,
                        "deleted_at": None,
                    },
                )
                changes["ws_level"] = {"old": old_ws, "new": data["ws_level"]}

        log_audit(
            organization=organization,
            action="member.role_updated",
            scope="organization",
            target_id=user_id,
            changes=changes,
        )

        return gm.success_response(
            {
                "message": "Role updated successfully.",
                "changes": changes,
            }
        )


class MemberRemoveAPIView(APIView):
    """
    DELETE /accounts/organization/members/remove/

    Soft-deactivates OrganizationMembership and cascades to workspace
    memberships.  Signals handle Redis clear + audit log.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdminOrWorkspaceAdmin,
        CanManageTargetUser,
    ]

    def delete(self, request):
        gm = GeneralMethods()
        serializer = MemberRemoveSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        organization = resolve_org(request)
        user_id = serializer.validated_data["user_id"]

        # Cannot remove yourself
        if str(request.user.id) == str(user_id):
            return gm.bad_request(get_error_message("CANNOT_REMOVE_SELF"))

        try:
            target_membership = OrganizationMembership.objects.get(
                user_id=user_id,
                organization=organization,
            )
        except OrganizationMembership.DoesNotExist:
            return gm.bad_request(get_error_message("MEMBER_NOT_IN_ORG"))

        with transaction.atomic():
            # Last owner guard (B4 fix: race-safe with select_for_update)
            if target_membership.level_or_legacy >= Level.OWNER:
                owner_count = (
                    OrganizationMembership.objects.select_for_update()
                    .filter(
                        organization=organization,
                        is_active=True,
                        level__gte=Level.OWNER,
                    )
                    .count()
                )
                # Also count legacy owners without level set
                legacy_owner_count = (
                    OrganizationMembership.objects.select_for_update()
                    .filter(
                        organization=organization,
                        is_active=True,
                        level__isnull=True,
                        role="Owner",
                    )
                    .count()
                )
                if (owner_count + legacy_owner_count) <= 1:
                    return gm.bad_request(get_error_message("LAST_OWNER_REMOVE"))

            # Soft-deactivate org membership (row preserved for audit trail
            # and future re-invite via update_or_create).
            target_membership.is_active = False
            target_membership.save(update_fields=["is_active"])

            # Soft-deactivate workspace memberships linked via org_membership FK
            WorkspaceMembership.objects.filter(
                organization_membership=target_membership,
                is_active=True,
            ).update(is_active=False)

            # Also deactivate ws memberships not linked via org_membership FK
            # (covers legacy rows where organization_membership was never set)
            target_user = User.objects.filter(id=user_id).first()
            if target_user:
                WorkspaceMembership.objects.filter(
                    user=target_user,
                    workspace__organization=organization,
                    is_active=True,
                ).update(is_active=False)

                # Invalidate session cache
                from accounts.views.workspace_management import clear_user_redis_cache

                clear_user_redis_cache(target_user.id)

        # Audit log (inline, since delete signals won't fire for soft-deactivate)
        log_audit(
            organization=organization,
            action="member.removed",
            scope="organization",
            target_id=user_id,
            changes={
                "role": target_membership.role,
                "level": target_membership.level,
            },
        )

        return gm.success_response(
            {
                "message": "Member removed from organization.",
                "user_id": str(user_id),
            }
        )


class MemberReactivateAPIView(APIView):
    """
    POST /accounts/organization/members/reactivate/

    Re-activates a deactivated org membership and restores workspace
    memberships that were soft-deactivated during removal.  If no prior
    workspace memberships exist, the user is added to the default workspace.
    """

    permission_classes = [
        IsAuthenticated,
        IsOrganizationAdminOrWorkspaceAdmin,
        CanManageTargetUser,
    ]

    def post(self, request):
        gm = GeneralMethods()
        serializer = MemberRemoveSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        organization = resolve_org(request)
        user_id = serializer.validated_data["user_id"]

        # Cannot reactivate yourself
        if str(request.user.id) == str(user_id):
            return gm.bad_request(get_error_message("CANNOT_REACTIVATE_SELF"))

        try:
            target_membership = OrganizationMembership.all_objects.get(
                user_id=user_id,
                organization=organization,
                is_active=False,
            )
        except OrganizationMembership.DoesNotExist:
            return gm.bad_request(get_error_message("NO_DEACTIVATED_MEMBERSHIP"))

        # D2: Verify actor's level is strictly above the target's stored level
        # (prevents Admin from reactivating a former Owner)
        actor_membership = get_org_membership(request.user)
        if not actor_membership:
            return gm.forbidden_response(get_error_message("NOT_ORG_MEMBER"))
        actor_level = actor_membership.level_or_legacy
        target_level = target_membership.level_or_legacy
        if actor_level < Level.OWNER and actor_level <= target_level:
            return gm.forbidden_response(
                get_error_message("REACTIVATE_LEVEL_FORBIDDEN")
            )

        # D1: Wrap DB operations in transaction.atomic()
        with transaction.atomic():
            # Re-activate org membership
            target_membership.is_active = True
            target_membership.save(update_fields=["is_active"])

            # Re-activate workspace memberships that were soft-deactivated during removal.
            ws_restored = WorkspaceMembership._base_manager.filter(
                user_id=user_id,
                workspace__organization=organization,
                is_active=False,
            ).update(is_active=True)

            if ws_restored == 0:
                default_ws = Workspace.objects.filter(
                    organization=organization, is_default=True
                ).first()
                if default_ws:
                    ws_level = Level.get_default_ws_level(
                        target_membership.level_or_legacy
                    )
                    WorkspaceMembership._base_manager.update_or_create(
                        workspace=default_ws,
                        user_id=user_id,
                        defaults={
                            "role": Level.to_ws_role(ws_level),
                            "level": ws_level,
                            "organization_membership": target_membership,
                            "is_active": True,
                            "deleted": False,
                            "deleted_at": None,
                        },
                    )

            # Reactivate user if they were globally deactivated
            target_user = User.objects.filter(id=user_id).first()
            if target_user:
                if not target_user.is_active:
                    target_user.is_active = True
                    target_user.save(update_fields=["is_active"])

                # Invalidate session cache
                from accounts.views.workspace_management import clear_user_redis_cache

                clear_user_redis_cache(target_user.id)

        log_audit(
            organization=organization,
            action="member.reactivated",
            scope="organization",
            target_id=user_id,
            changes={
                "role": target_membership.role,
                "level": target_membership.level,
            },
        )

        return gm.success_response(
            {
                "message": "Member reactivated.",
                "user_id": str(user_id),
            }
        )


# =====================================================================
# Workspace-scoped member endpoints
# =====================================================================


class WorkspaceMemberListAPIView(APIView):
    """
    GET /accounts/workspace/<workspace_id>/members/

    Returns members of a specific workspace.
    Org Admin+ users who auto-access are included with derived WS Admin role.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, workspace_id):
        gm = GeneralMethods()
        organization = resolve_org(request)
        if not organization:
            return gm.bad_request(get_error_message("USER_NOT_IN_ORG"))

        # Verify workspace exists and belongs to the org
        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return gm.bad_request(get_error_message("WORKSPACE_NOT_FOUND"))

        # Permission: must be WS Admin or Org Admin+
        org_membership = get_org_membership(request.user)
        org_level = org_membership.level_or_legacy if org_membership else 0
        if org_level < Level.ADMIN:
            ws_level = get_effective_workspace_level(request.user, workspace_id)
            if ws_level is None or ws_level < Level.WORKSPACE_ADMIN:
                return gm.forbidden_response(get_error_message("WS_ADMIN_REQUIRED"))

        # Pre-process query params: parse JSON-encoded list params
        query_data = request.query_params.copy()
        for list_field in (
            "filter_status",
            "filterStatus",
            "filter_role",
            "filterRole",
        ):
            raw = query_data.get(list_field)
            if raw and isinstance(raw, str) and raw.startswith("["):
                try:
                    query_data.setlist(list_field, json.loads(raw))
                except (ValueError, TypeError):
                    pass
        # Normalize camelCase → snake_case for query params
        if "filterStatus" in query_data and "filter_status" not in query_data:
            query_data.setlist("filter_status", query_data.getlist("filterStatus"))
        if "filterRole" in query_data and "filter_role" not in query_data:
            query_data.setlist("filter_role", query_data.getlist("filterRole"))

        serializer = WorkspaceMemberListRequestSerializer(data=query_data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)
        params = serializer.validated_data

        # 1. Get explicit workspace members
        # Filter on org membership active status to exclude users who were
        # deactivated at the org level (covers legacy rows where ws.is_active
        # was not cascaded during removal).
        ws_memberships = (
            WorkspaceMembership.objects.filter(
                workspace=workspace, is_active=True, user__is_active=True
            )
            .exclude(organization_membership__is_active=False)
            .select_related("user", "organization_membership")
        )

        explicit_user_ids = set()
        results = []
        for ws_mem in ws_memberships:
            user = ws_mem.user
            explicit_user_ids.add(user.id)
            org_mem = ws_mem.organization_membership
            results.append(
                {
                    "id": str(user.id),
                    "name": user.name or "",
                    "email": user.email,
                    "ws_level": ws_mem.level_or_legacy,
                    "ws_role": Level.to_ws_string(ws_mem.level_or_legacy),
                    "org_level": org_mem.level_or_legacy if org_mem else None,
                    "org_role": (
                        Level.to_org_string(org_mem.level_or_legacy)
                        if org_mem
                        else None
                    ),
                    "status": "Active",
                    "created_at": (
                        ws_mem.created_at.isoformat()
                        if hasattr(ws_mem, "created_at") and ws_mem.created_at
                        else ""
                    ),
                    "type": "member",
                }
            )

        # 2. Add Org Admin+ users who auto-access (no explicit WS membership)
        org_admins = (
            OrganizationMembership.objects.filter(
                organization=organization,
                is_active=True,
                user__is_active=True,
            )
            .filter(
                models.Q(level__gte=Level.ADMIN)
                | models.Q(level__isnull=True, role__in=["Admin", "Owner"])
            )
            .select_related("user")
        )

        for org_mem in org_admins:
            if org_mem.user_id not in explicit_user_ids:
                user = org_mem.user
                results.append(
                    {
                        "id": str(user.id),
                        "name": user.name or "",
                        "email": user.email,
                        "ws_level": Level.WORKSPACE_ADMIN,
                        "ws_role": "Workspace Admin",
                        "org_level": org_mem.level_or_legacy,
                        "org_role": Level.to_org_string(org_mem.level_or_legacy),
                        "status": "Active",
                        "created_at": (
                            org_mem.joined_at.isoformat() if org_mem.joined_at else ""
                        ),
                        "type": "member",
                        "auto_access": True,
                    }
                )

        # 3. Add pending/expired invites for this workspace
        invites = self._get_workspace_invites(organization, workspace)
        invited_emails = {inv["email"] for inv in invites}
        # Deduplicate: remove active member rows whose email has a pending invite
        results = [r for r in results if r["email"] not in invited_emails]
        results.extend(invites)

        # Apply search
        search = params.get("search", "").lower()
        if search:
            results = [
                r
                for r in results
                if search in r.get("name", "").lower()
                or search in r.get("email", "").lower()
            ]

        # Apply status filter
        filter_status = params.get("filter_status", [])
        if filter_status:
            results = [r for r in results if r["status"] in filter_status]

        # Apply role filter
        filter_role = params.get("filter_role", [])
        if filter_role:
            ws_levels = set()
            for val in filter_role:
                val = str(val)
                if val.startswith("ws_"):
                    ws_levels.add(int(val[3:]))
                else:
                    try:
                        ws_levels.add(int(val))
                    except ValueError:
                        pass
            if ws_levels:
                results = [r for r in results if r.get("ws_level") in ws_levels]

        # Sort
        ALLOWED_SORT_FIELDS = {
            "name",
            "email",
            "role",
            "level",
            "status",
            "type",
            "date_joined",
            "created_at",
        }
        sort_field = params.get("sort", "-created_at")
        reverse = sort_field.startswith("-")
        sort_key = sort_field.lstrip("-")
        if sort_key not in ALLOWED_SORT_FIELDS:
            sort_key = "name"
        results.sort(key=lambda r: r.get(sort_key, ""), reverse=reverse)

        # Paginate
        page = params.get("page", 1)
        limit = params.get("limit", 20)
        start = (page - 1) * limit
        end = start + limit

        return gm.success_response(
            {
                "results": results[start:end],
                "total": len(results),
                "page": page,
                "limit": limit,
            }
        )

    def _get_workspace_invites(self, organization, workspace):
        """Return pending/expired invites that include this workspace."""
        invites = OrganizationInvite.objects.filter(
            organization=organization,
            status=InviteStatus.PENDING,
        )
        results = []
        for inv in invites:
            # Check if invite's workspace_access includes this workspace
            ws_match = None
            if inv.workspace_access:
                for ws_entry in inv.workspace_access:
                    if str(ws_entry.get("workspace_id")) == str(workspace.id):
                        ws_match = ws_entry
                        break
            # Also include Admin+ invites (they auto-access all workspaces)
            if ws_match is None and inv.level < Level.ADMIN:
                continue

            ws_level = (
                ws_match.get("level", Level.WORKSPACE_ADMIN)
                if ws_match
                else Level.WORKSPACE_ADMIN
            )
            results.append(
                {
                    "id": str(inv.id),
                    "name": inv.target_email.split("@")[0],
                    "email": inv.target_email,
                    "ws_level": ws_level,
                    "ws_role": Level.to_ws_string(ws_level),
                    "org_level": inv.level,
                    "org_role": Level.to_org_string(inv.level),
                    "status": inv.effective_status,  # "Pending" or "Expired"
                    "created_at": (
                        inv.created_at.isoformat() if inv.created_at else ""
                    ),
                    "type": "invite",
                }
            )
        return results


class WorkspaceMemberRoleUpdateAPIView(APIView):
    """
    POST /accounts/workspace/<workspace_id>/members/role/

    Update a member's workspace role.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, workspace_id):
        gm = GeneralMethods()
        organization = resolve_org(request)
        if not organization:
            return gm.bad_request(get_error_message("USER_NOT_IN_ORG"))

        from tfc.ee_gating import EEFeature, check_ee_feature

        check_ee_feature(EEFeature.CUSTOM_ROLES, org_id=str(organization.id))

        # Verify workspace
        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return gm.bad_request(get_error_message("WORKSPACE_NOT_FOUND"))

        # Permission: must be WS Admin or Org Admin+
        org_membership = get_org_membership(request.user)
        org_level = org_membership.level_or_legacy if org_membership else 0
        if org_level < Level.ADMIN:
            actor_ws_level = get_effective_workspace_level(request.user, workspace_id)
            if actor_ws_level is None or actor_ws_level < Level.WORKSPACE_ADMIN:
                return gm.forbidden_response(get_error_message("WS_ADMIN_REQUIRED"))

        serializer = WorkspaceMemberRoleUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        data = serializer.validated_data
        user_id = data["user_id"]
        new_ws_level = data["ws_level"]

        # Cannot modify Org Admin+ workspace roles (they auto-have WS Admin)
        target_org_membership = OrganizationMembership.objects.filter(
            user_id=user_id, organization=organization
        ).first()
        if (
            target_org_membership
            and target_org_membership.level_or_legacy >= Level.ADMIN
        ):
            return gm.bad_request(get_error_message("WS_ROLE_MODIFY_ORG_ADMIN"))

        try:
            ws_membership = WorkspaceMembership.no_workspace_objects.get(
                workspace=workspace, user_id=user_id, is_active=True
            )
        except WorkspaceMembership.DoesNotExist:
            return gm.bad_request(get_error_message("WS_MEMBER_NOT_FOUND"))

        # B2 fix: Verify actor's workspace level is strictly above target's
        target_ws_level = ws_membership.level_or_legacy
        if org_level < Level.ADMIN:
            actor_ws_level = get_effective_workspace_level(request.user, workspace_id)
            if actor_ws_level is None or actor_ws_level <= target_ws_level:
                return gm.forbidden_response(
                    get_error_message("WS_ROLE_MODIFY_FORBIDDEN")
                )
            # Issue 9 fix: prevent non-Org-Admin from setting level >= their own
            if new_ws_level >= actor_ws_level:
                return gm.forbidden_response(
                    get_error_message("WS_ROLE_ASSIGN_FORBIDDEN")
                )

        old_level = ws_membership.level_or_legacy
        ws_membership.level = new_ws_level
        ws_membership.role = Level.to_ws_role(new_ws_level)
        ws_membership.save(update_fields=["level", "role"])

        log_audit(
            organization=organization,
            action="workspace_member.role_updated",
            scope="workspace",
            target_id=user_id,
            changes={
                "workspace_id": str(workspace_id),
                "ws_level": {"old": old_level, "new": new_ws_level},
            },
        )

        return gm.success_response(
            {
                "message": "Workspace role updated successfully.",
                "user_id": str(user_id),
                "ws_level": new_ws_level,
                "ws_role": Level.to_ws_string(new_ws_level),
            }
        )


class WorkspaceMemberRemoveAPIView(APIView):
    """
    DELETE /accounts/workspace/<workspace_id>/members/remove/

    Remove a member from a workspace only (keeps org membership).
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request, workspace_id):
        gm = GeneralMethods()
        organization = resolve_org(request)
        if not organization:
            return gm.bad_request(get_error_message("USER_NOT_IN_ORG"))

        # Verify workspace
        try:
            workspace = Workspace.objects.get(
                id=workspace_id, organization=organization, is_active=True
            )
        except Workspace.DoesNotExist:
            return gm.bad_request(get_error_message("WORKSPACE_NOT_FOUND"))

        # Permission: must be WS Admin or Org Admin+
        org_membership = get_org_membership(request.user)
        org_level = org_membership.level_or_legacy if org_membership else 0
        if org_level < Level.ADMIN:
            actor_ws_level = get_effective_workspace_level(request.user, workspace_id)
            if actor_ws_level is None or actor_ws_level < Level.WORKSPACE_ADMIN:
                return gm.forbidden_response(get_error_message("WS_ADMIN_REQUIRED"))

        serializer = WorkspaceMemberRemoveSerializer(data=request.data)
        if not serializer.is_valid():
            return gm.bad_request(serializer.errors)

        user_id = serializer.validated_data["user_id"]

        # Cannot remove yourself
        if str(request.user.id) == str(user_id):
            return gm.bad_request(get_error_message("CANNOT_REMOVE_SELF_FROM_WS"))

        # Cannot remove Org Admin+ (they auto-access all workspaces)
        target_org_membership = OrganizationMembership.objects.filter(
            user_id=user_id, organization=organization
        ).first()
        if (
            target_org_membership
            and target_org_membership.level_or_legacy >= Level.ADMIN
        ):
            return gm.bad_request(get_error_message("CANNOT_REMOVE_ORG_ADMIN_FROM_WS"))

        try:
            ws_membership = WorkspaceMembership.no_workspace_objects.get(
                workspace=workspace, user_id=user_id, is_active=True
            )
        except WorkspaceMembership.DoesNotExist:
            return gm.bad_request(get_error_message("WS_MEMBER_NOT_FOUND"))

        # Block removal if this is the user's last workspace in the org.
        # Every org member must have at least one workspace.
        # Use no_workspace_objects to count across ALL workspaces (not just current).
        active_ws_count = WorkspaceMembership.no_workspace_objects.filter(
            user_id=user_id,
            workspace__organization=organization,
            is_active=True,
        ).count()
        if active_ws_count <= 1:
            return gm.bad_request(get_error_message("CANNOT_REMOVE_LAST_WS"))

        ws_membership.delete()

        log_audit(
            organization=organization,
            action="workspace_member.removed",
            scope="workspace",
            target_id=user_id,
            changes={"workspace_id": str(workspace_id)},
        )

        return gm.success_response(
            {
                "message": "Member removed from workspace.",
                "user_id": str(user_id),
            }
        )

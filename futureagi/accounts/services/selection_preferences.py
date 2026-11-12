"""Persist validated context selections without saving cached user preferences."""

from django.db import transaction

from accounts.models.user import User


def save_switch_preferences(
    user, organization, workspace, *, switch_organization=False
):
    """Merge only the switch's keys into the latest config under a shared row lock.

    Callers authorize the organization/workspace first. Workspace-only switches
    leave the saved organization unchanged; only they update the legacy default.
    """
    with transaction.atomic():
        stored_user = User.objects.select_for_update().get(pk=user.pk)
        config = stored_user.config or {}
        if switch_organization:
            config["currentOrganizationId"] = str(organization.pk)
            config["selected_organization_id"] = str(organization.pk)
        if workspace is not None:
            workspace_id = str(workspace.pk)
            config["currentWorkspaceId"] = workspace_id
            if not switch_organization:
                config["defaultWorkspaceId"] = workspace_id
            org_workspace_map = config.get("orgWorkspaceMap", {})
            org_workspace_map[str(organization.pk)] = workspace_id
            config["orgWorkspaceMap"] = org_workspace_map
        stored_user.config = config
        stored_user.save(update_fields=["config"])
    user.config = config

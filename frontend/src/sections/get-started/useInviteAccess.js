import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useWorkspaceFromList } from "src/api/workspaces/list";
import { canInviteMembers } from "src/layouts/dashboard/WorkspaceSwitcher/orgCreateReason";

// The roles the organization Members route lets in (the route table,
// routes/sections/dashboard.jsx). Its guard lets a reader in when their
// organization role or their current workspace role is one of them. A test
// reads the route table and fails when the two differ.
export const MEMBERS_ROLES = ["Owner", "Admin"];

// The roles the workspace Members route lets in (the same route table). Its
// guard maps the reader's level in that workspace to one of these roles. A
// test reads the route table and fails when the two differ.
export const WORKSPACE_MEMBERS_ROLES = ["workspace_admin"];

// A workspace's Members page, at the path the route table and the settings
// menu give it.
export const workspaceMembersPath = (workspaceId) =>
  `/dashboard/settings/workspace/${workspaceId}/members`;

// The workspace role a level in that workspace stands for, as the workspace
// route guard maps it.
const workspaceRoleFor = (level) => {
  if (level >= 8) return "workspace_admin";
  if (level >= 3) return "workspace_member";
  if (level >= 1) return "workspace_viewer";
  return null;
};

// What this reader can do about inviting teammates on Get started.
// - isOwner: the organization Owner. The invite form on the step is theirs
//   only: it sends a request only the Owner may send.
// - canInvite: they can invite anywhere: the workspace switcher offers them
//   its "Invite members", by the switcher's own check.
// - opensMembers: the organization Members route lets them in.
// - countsInvite: the invite step counts toward their progress.
export const useInviteAccess = () => {
  const { user, role } = useAuthContext() ?? {};
  const { currentWorkspaceRole } = useWorkspace() ?? {};
  const { currentOrganizationRole } = useOrganization() ?? {};
  const orgRole = user?.organization_role;
  const isOwner = (orgRole ?? user?.organizationRole) === "Owner";
  const canInvite = canInviteMembers({
    currentWorkspaceRole,
    user,
    currentOrganizationRole,
  });
  const opensMembers = [orgRole, role].some(
    (each) => Boolean(each) && MEMBERS_ROLES.includes(each),
  );
  return {
    isOwner,
    canInvite,
    opensMembers,
    countsInvite: isOwner || canInvite,
  };
};

// The current workspace's Members page, and whether its route lets this
// reader in. The route is registered only where workspaces are on; its guard
// reads the reader's level in that workspace from the workspace list. (It
// also lets an organization Owner or Admin in without the list; the step
// sends them to the organization Members page first, so that branch is not
// read here.) The page is named as the settings menu names it: the
// workspace's name, then "Members". Reads the list only when enabled.
export const useWorkspaceMembersPage = ({ enabled = true } = {}) => {
  const { user } = useAuthContext() ?? {};
  const { currentWorkspaceId } = useWorkspace() ?? {};
  const registered = Boolean(user?.ws_enabled) && Boolean(currentWorkspaceId);
  const reads = enabled && registered;
  const { workspace, isLoading } = useWorkspaceFromList(currentWorkspaceId, {
    enabled: reads,
  });
  const admits =
    reads &&
    Boolean(workspace) &&
    WORKSPACE_MEMBERS_ROLES.includes(
      workspaceRoleFor(workspace.user_ws_level || 0),
    );
  return {
    admits,
    isLoading: reads && Boolean(isLoading),
    path: workspaceMembersPath(currentWorkspaceId),
    name: workspace?.display_name || workspace?.name || "Workspace",
  };
};

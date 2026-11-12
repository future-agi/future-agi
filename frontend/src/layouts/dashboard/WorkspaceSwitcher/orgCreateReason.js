// Shown wherever organization creation is disabled for a single sign-on
// (SAML-scoped) session: the server refuses it with saml_scope_conflict.
export const ORG_CREATE_SSO_REASON =
  "Creating organizations isn't available in single sign-on sessions.";

// Whether the workspace switcher offers "Invite members": the current
// workspace role (else the default one from user-info) or the organization
// role is an owner's or an admin's.
const allowedToInvite = ["Owner", "Admin", "workspace_admin"];
export const canInviteMembers = ({
  currentWorkspaceRole,
  user,
  currentOrganizationRole,
}) =>
  allowedToInvite.includes(
    currentWorkspaceRole || user?.default_workspace_role,
  ) || allowedToInvite.includes(currentOrganizationRole);

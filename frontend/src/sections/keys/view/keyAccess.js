// L7c g1 (nav:keys): who may create and manage API keys, by the same rule as
// the Keys nav entry (config-navigation.jsx: org Owner or Admin, or a
// workspace admin). A viewer or member sees no Add API Key and no key
// actions. Pure.
export function canManageKeys(orgRole, workspaceRole) {
  return (
    orgRole === "Owner" ||
    orgRole === "Admin" ||
    workspaceRole === "workspace_admin"
  );
}

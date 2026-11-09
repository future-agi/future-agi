// Plain-English role names for the no-access state (no-access-state.jsx).

const ROLE_LABELS = {
  Owner: "Owner",
  Admin: "Admin",
  Member: "Member",
  Viewer: "Viewer",
  workspace_admin: "Admin",
  workspace_member: "Member",
  workspace_viewer: "Viewer",
};

const ORG_ROLES = ["Owner", "Admin", "Member", "Viewer"];
const WORKSPACE_ROLES = [
  "workspace_admin",
  "workspace_member",
  "workspace_viewer",
];

function joinOr(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

export function describeNeededRoles(allowedRoles = []) {
  const org = ORG_ROLES.filter((r) => allowedRoles.includes(r));
  const ws = WORKSPACE_ROLES.filter((r) => allowedRoles.includes(r)).map(
    (r) => ROLE_LABELS[r],
  );
  const parts = [];
  if (org.length) {
    parts.push(`the organization role ${joinOr(org)}`);
  }
  if (ws.length) {
    parts.push(`the workspace role ${joinOr(ws)}`);
  }
  return parts.join(", or ");
}

export function describeRole({ orgRole, workspaceRole }) {
  const parts = [];
  if (orgRole)
    parts.push(`${ROLE_LABELS[orgRole] || orgRole} in this organization`);
  if (workspaceRole && WORKSPACE_ROLES.includes(workspaceRole)) {
    parts.push(`${ROLE_LABELS[workspaceRole]} in this workspace`);
  }
  return parts.join(" and ");
}

// Integer-based role options matching backend Level constants
export const orgRoleOptions = [
  { label: "Owner", value: 15 },
  { label: "Admin", value: 8 },
  { label: "Member", value: 3 },
  { label: "Viewer", value: 1 },
];

export const wsRoleOptions = [
  { label: "Workspace Admin", value: 8 },
  { label: "Workspace Member", value: 3 },
  { label: "Workspace Viewer", value: 1 },
];

// Level constants (mirror backend tfc/constants/levels.py)
// Workspace levels are aliased to the same integers (same scale, different scope)
export const LEVELS = {
  OWNER: 15,
  ADMIN: 8,
  MEMBER: 3,
  VIEWER: 1,
  WORKSPACE_ADMIN: 8,
  WORKSPACE_MEMBER: 3,
  WORKSPACE_VIEWER: 1,
};

// Map integer levels to display strings
export const orgLevelToString = {
  15: "Owner",
  8: "Admin",
  3: "Member",
  1: "Viewer",
};

export const wsLevelToString = {
  8: "Workspace Admin",
  3: "Workspace Member",
  1: "Workspace Viewer",
};

// Status chip styles
export const cellStyles = {
  Pending: {
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
  Expired: {
    backgroundColor: "status.danger.bg",
    color: "status.danger.fg",
  },
  Active: {
    backgroundColor: "status.success.bg",
    color: "status.success.fg",
  },
  Deactivated: {
    backgroundColor: "action.selected",
    color: "text.disabled",
  },
};

// Org role chip styles
export const orgRoleCellStyles = {
  Owner: {
    backgroundColor: "status.discovery.bg",
    color: "status.discovery.fg",
  },
  Admin: {
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
  Member: {
    backgroundColor: "action.selected",
    color: "text.secondary",
  },
  Viewer: {
    backgroundColor: "action.selected",
    color: "text.disabled",
  },
};

// Workspace role chip styles
export const wsRoleCellStyles = {
  "Workspace Admin": {
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
  "Workspace Member": {
    backgroundColor: "action.selected",
    color: "text.secondary",
  },
  "Workspace Viewer": {
    backgroundColor: "action.selected",
    color: "text.disabled",
  },
};

// Action menus keyed by status
const pendingExpiredMenus = [
  {
    title: "Resend the invite",
    image: "/assets/icons/resend-invite.svg",
    color: "text.primary",
    action: "resend-invite",
  },
  {
    title: "Cancel invite",
    image: "/assets/icons/deactivate.svg",
    color: "status.danger.fg",
    action: "cancel-invite",
  },
];

export const actionMenusByStatus = {
  Active: [
    {
      title: "Edit user info",
      image: "/assets/icons/ic_edit_pencil.svg",
      color: "text.primary",
      action: "edit-role",
    },
    {
      title: "Remove from organization",
      image: "/assets/icons/deactivate.svg",
      color: "status.danger.fg",
      action: "remove-member",
    },
  ],
  Pending: pendingExpiredMenus,
  Expired: pendingExpiredMenus,
  Deactivated: [
    {
      title: "Reactivate member",
      image: "/assets/icons/resend-invite.svg",
      color: "text.primary",
      action: "reactivate-member",
    },
  ],
};

// Workspace-scoped action menus
export const wsActionMenusByStatus = {
  Active: [
    {
      title: "Edit user info",
      image: "/assets/icons/ic_edit_pencil.svg",
      color: "text.primary",
      action: "edit-ws-role",
    },
    {
      title: "Remove from workspace",
      image: "/assets/icons/deactivate.svg",
      color: "status.danger.fg",
      action: "remove-ws-member",
    },
  ],
  Pending: pendingExpiredMenus,
  Expired: pendingExpiredMenus,
};

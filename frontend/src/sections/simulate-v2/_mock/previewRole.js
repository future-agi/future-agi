/*
  Workspace roles (roadmap "How the product fits together" → Roles,
  permissions and sharing), previewed by an admin from the workspace menu.

    Viewer — browses worlds, agents, scenarios and evidence; can't start a
             paid run, change a definition or see a secret value.
    Member — drafts, copies, runs within assigned budgets; can't change the
             workspace's pricing or raise an admin's limits.
    Admin  — everything, including budgets and membership.

  The product's own roles are workspace_admin / workspace_member /
  workspace_viewer. Here the signed-in person is an admin who can look at
  the product as each role sees it; the choice is kept for this browser.
*/
import { create } from "zustand";
import { persist } from "zustand/middleware";

export const ROLES = [
  { id: "admin", label: "Admin", note: "You — everything, including budgets and members" },
  { id: "member", label: "Member", note: "Runs within budgets; can't change pricing or limits" },
  { id: "viewer", label: "Viewer", note: "Looks at everything; can't run, edit or see secrets" },
];
export const roleLabel = (id) => ROLES.find((r) => r.id === id)?.label || "Admin";

export const usePreviewRole = create(
  persist(
    (set) => ({
      role: "admin",
      setRole: (role) => set({ role }),
    }),
    { name: "fagi.previewRole" },
  ),
);

/** What a role may do here, with the line that says why not. */
export const gatesOf = (role) => {
  const viewer = role === "viewer";
  return {
    role,
    canRun: !viewer,
    canEdit: !viewer,
    isAdmin: role === "admin",
    runReason: viewer ? "Viewers can't start paid runs — ask a workspace admin for Member access" : "",
    editReason: viewer ? "View only — viewers can't change this. Ask a workspace admin for Member access." : "",
  };
};

/** The current role's gates, for a component. */
export const useRoleGates = () => gatesOf(usePreviewRole((s) => s.role));
